#!/usr/bin/env python3

"""
A Python 3 application for ripping entire YouTube accounts with metadata.

The functions and classes in this module are hopefully reusable in some
fashion and hopefully understandable. The script can be run to
rip YouTube accounts.
"""

"""
Copyright (c) 2013, w0rp <devw0rp@gmail.com>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
"""

from itertools import count, repeat, cycle
from functools import partial, reduce
import operator

import os
import sys
import shutil
import re
import json
import datetime
import time
import socket

PYTHON_2 = sys.version_info[0] == 2

if PYTHON_2:
    # Python 2 has a different module structure for network functions.
    from urllib import urlencode
    from urlparse import parse_qs
    from urllib2 import Request
    from urllib2 import HTTPError

    import contextlib
    import urllib2

    urlopen = lambda *args, **kwargs: contextlib.closing(
        urllib2.urlopen(*args, **kwargs)
    )

    compat_str = basestring
else:
    from urllib.parse import urlencode, parse_qs
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    compat_str = str

API_URL = "https://gdata.youtube.com/feeds/api"
INFO_URL = "https://www.youtube.com/get_video_info"
MAX_RESULTS = 50

FILETYPE_RATING_DICT = {
    "3gp": 1,
    "flv": 2,
    "mp4": 3,
    "webm": 4,
}

VIDEO_ID_REGEX = re.compile("^[\w\-]{11}$")

JSON_FORMAT_VERSION = "1.0"

# A function for computing the product of a sequence.
product = partial(reduce, operator.mul)

class FeedItem:
    """
    This object represents a feed item taken from a video feed.
    """
    def __init__(self, video_id, upload_time, title, description):
        assert VIDEO_ID_REGEX.match(video_id)
        assert isinstance(upload_time, datetime.datetime)
        assert isinstance(title, compat_str)
        assert isinstance(description, compat_str)

        self.video_id = video_id
        self.upload_time = upload_time
        self.title = title
        self.description = description

    def to_json(self):
        return {
            "video_id": self.video_id,
            "upload_time": to_epoch(self.upload_time),
            "title": self.title,
            "description": self.description,
        }

class MediaType:
    """
    This object holds information about a media type, including
    bitrate, file type, etc.
    """
    def __init__(self, itag, file_type, resolution, video_format,
    video_bitrate, audio_format, audio_bitrate):
        assert isinstance(itag, int)
        assert isinstance(file_type, compat_str)

        assert video_format is None or isinstance(video_format, compat_str)

        if video_format is not None:
            assert isinstance(resolution, tuple)
            assert len(resolution) == 2
            assert isinstance(resolution[0], int)
            assert isinstance(resolution[1], int)
            assert isinstance(video_bitrate, (int, float))

        assert audio_format is None or isinstance(audio_format, compat_str)

        if audio_format is not None:
            assert isinstance(audio_bitrate, (int, float))

        self.__itag = itag
        self.__file_type = file_type
        self.__resolution = resolution
        self.__video_format = video_format
        self.__video_bitrate = video_bitrate
        self.__audio_format = audio_format
        self.__audio_bitrate = audio_bitrate

    def __hash__(self):
        return self.__itag

    def __eq__(self, other):
        return self.__itag == other.__itag

    @property
    def itag(self):
        """
        Return the itag for this object.

        This is an ID code identifying the media type.
        """
        return self.__itag

    @property
    def file_type(self):
        """
        Return the file_type for this medium.

        This is a valid file extension.
        """
        return self.__file_type

    @property
    def resolution(self):
        """
        Return the resolution of the video.

        This is a pair width, height.
        """
        assert self.has_video

        return self.__resolution

    @property
    def has_video(self):
        """
        Return True if this media has video content.
        """
        return self.__video_format is not None

    @property
    def has_audio(self):
        """
        Return True if this media has audio content.
        """
        return self.__audio_format is not None

    @property
    def video_bitrate(self):
        """
        Return the bitrate for the video content, in Mbit/s

        This is the minimum bitrate for the video content.
        """
        assert self.has_video

        return self.__video_bitrate

    @property
    def audio_bitrate(self):
        """
        Return the bitrate for the audio content, in kbit/s

        This is the minimum bitrate for the audio content.
        """
        assert self.has_audio

        return self.__video_bitrate

    def to_json(self):
        return {
            "itag": self.__itag,
            "file_type": self.__file_type,
            "resolution": self.__resolution,
            "video_format": self.__video_format,
            "video_bitrate": self.__video_bitrate,
            "audio_format": self.__audio_format,
            "audio_bitrate": self.__audio_bitrate,
        }

class DownloadInfo:
    """
    This object encapsulates information for a downloadable piece of media.

    URLs will expire after a short time.
    """
    def __init__(self, media_type, url):
        self.media_type = media_type
        self.url = url

    def to_json(self):
        return {
            "media_type" : self.media_type.to_json(),
        }

# TODO: Add maximum video bitrate here?
ITAG_MAP = {
    media_type.itag: media_type
    for media_type in
    (
        MediaType(
            itag= 5,
            file_type= "flv",
            resolution= (320, 240),
            video_format= "Sorenson h.263",
            video_bitrate= 0.25,
            audio_format= "mp3",
            audio_bitrate= 64
        ),
        MediaType(
            itag= 6,
            file_type= "flv",
            resolution= (400, 270),
            video_format= "Sorenson h.263",
            video_bitrate= 0.8,
            audio_format= "mp3",
            audio_bitrate= 64
        ),
        MediaType(
            itag= 13,
            file_type= "3gp",
            # FIXME: This is a guess, and it may be wrong.
            resolution= (400, 270),
            video_format= "MPEG-4 Visual",
            video_bitrate= 0.5,
            audio_format= "aac",
            # FIXME: This is a guess, and it may be wrong.
            audio_bitrate= 24
        ),
        MediaType(
            itag= 17,
            file_type= "3gp",
            resolution= (176, 144),
            video_format= "MPEG-4 Visual",
            video_bitrate= 0.05,
            audio_format= "aac",
            audio_bitrate= 24
        ),
        MediaType(
            itag= 18,
            file_type= "mp4",
            resolution= (400, 270),
            video_format= "h.264",
            video_bitrate= 0.5,
            audio_format= "aac",
            audio_bitrate= 96
        ),
        MediaType(
            itag= 22,
            file_type= "mp4",
            resolution= (1280, 720),
            video_format= "h.264",
            video_bitrate= 2,
            audio_format= "aac",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 34,
            file_type= "flv",
            resolution= (480, 360),
            video_format= "h.264",
            video_bitrate= 0.5,
            audio_format= "aac",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 35,
            file_type= "flv",
            resolution= (640, 480),
            video_format= "h.264",
            video_bitrate= 0.8,
            audio_format= "aac",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 36,
            file_type= "3gp",
            resolution= (320, 240),
            video_format= "MPEG-4 Visual",
            video_bitrate= 0.17,
            audio_format= "aac",
            audio_bitrate= 38
        ),
        MediaType(
            itag= 37,
            file_type= "mp4",
            resolution= (1920, 1080),
            video_format= "h.264",
            video_bitrate= 3,
            audio_format= "aac",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 38,
            file_type= "mp4",
            resolution= (4096, 3072),
            video_format= "h.264",
            video_bitrate= 3.5,
            audio_format= "aac",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 43,
            file_type= "webm",
            resolution= (480, 360),
            video_format= "vp8",
            video_bitrate= 0.5,
            audio_format= "vorbis",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 44,
            file_type= "webm",
            resolution= (640, 480),
            video_format= "vp8",
            video_bitrate= 1,
            audio_format= "vorbis",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 45,
            file_type= "webm",
            resolution= (1280, 720),
            video_format= "vp8",
            video_bitrate= 2,
            audio_format= "vorbis",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 46,
            file_type= "webm",
            resolution= (1920, 1080),
            video_format= "vp8",
            # FIXME: This is a guess, and it may be wrong.
            video_bitrate= 3,
            audio_format= "vorbis",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 82,
            file_type= "mp4",
            resolution= (480, 360),
            video_format= "h.264",
            video_bitrate= 0.5,
            audio_format= "aac",
            audio_bitrate= 96
        ),
        MediaType(
            itag= 83,
            file_type= "mp4",
            resolution= (320, 240),
            video_format= "h.264",
            video_bitrate= 0.5,
            audio_format= "aac",
            audio_bitrate= 96
        ),
        MediaType(
            itag= 84,
            file_type= "mp4",
            resolution= (1280, 720),
            video_format= "h.264",
            video_bitrate= 2,
            audio_format= "aac",
            audio_bitrate= 152
        ),
        MediaType(
            itag= 85,
            file_type= "mp4",
            resolution= (576, 520),
            video_format= "h.264",
            video_bitrate= 2,
            audio_format= "aac",
            audio_bitrate= 152
        ),
        MediaType(
            itag= 100,
            file_type= "webm",
            resolution= (480, 360),
            video_format= "vp8",
            # FIXME: This is a guess, and it may be wrong.
            video_bitrate= 0.5,
            audio_format= "vorbis",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 101,
            file_type= "webm",
            resolution= (480, 360),
            video_format= "vp8",
            # FIXME: This is a guess, and it may be wrong.
            video_bitrate= 0.5,
            audio_format= "vorbis",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 102,
            file_type= "webm",
            resolution= (1280, 720),
            video_format= "vp8",
            # FIXME: This is a guess, and it may be wrong.
            video_bitrate= 2,
            audio_format= "vorbis",
            audio_bitrate= 192
        ),
        MediaType(
            itag= 120,
            file_type= "flv",
            resolution= (1280, 720),
            video_format= "h.264",
            video_bitrate= 2,
            audio_format= "aac",
            audio_bitrate= 128
        ),
        # 133 to 137 are video only streams.
        MediaType(
            itag= 133,
            file_type= "mp4",
            resolution= (320, 240),
            video_format= "h.264",
            video_bitrate= 0.2,
            audio_format= None,
            audio_bitrate= None
        ),
        MediaType(
            itag= 134,
            file_type= "mp4",
            resolution= (480, 360),
            video_format= "h.264",
            video_bitrate= 0.3,
            audio_format= None,
            audio_bitrate= None
        ),
        MediaType(
            itag= 135,
            file_type= "mp4",
            resolution= (640, 480),
            video_format= "h.264",
            video_bitrate= 0.5,
            audio_format= None,
            audio_bitrate= None
        ),
        MediaType(
            itag= 136,
            file_type= "mp4",
            resolution= (1280, 720),
            video_format= "h.264",
            video_bitrate= 1,
            audio_format= None,
            audio_bitrate= None
        ),
        MediaType(
            itag= 137,
            file_type= "mp4",
            resolution= (1920, 1080),
            video_format= "h.264",
            video_bitrate= 2,
            audio_format= None,
            audio_bitrate= None
        ),
        # 139-141 are for audio only streams.
        MediaType(
            itag= 139,
            file_type= "mp4",
            resolution= None,
            video_format= None,
            video_bitrate= None,
            audio_format= "aac",
            audio_bitrate= 48
        ),
        MediaType(
            itag= 140,
            file_type= "mp4",
            resolution= None,
            video_format= None,
            video_bitrate= None,
            audio_format= "aac",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 141,
            file_type= "mp4",
            resolution= None,
            video_format= None,
            video_bitrate= None,
            audio_format= "aac",
            audio_bitrate= 256
        ),
        # 160 is another video-only stream.
        MediaType(
            itag= 160,
            file_type= "mp4",
            resolution= (176, 144),
            video_format= "h.264",
            video_bitrate= 0.1,
            audio_format= None,
            audio_bitrate= None
        ),
        # 171-172 are audio only streams.
        MediaType(
            itag= 171,
            file_type= "webm",
            resolution= None,
            video_format= None,
            video_bitrate= None,
            audio_format= "vorbis",
            audio_bitrate= 128
        ),
        MediaType(
            itag= 172,
            file_type= "webm",
            resolution= None,
            video_format= None,
            video_bitrate= None,
            audio_format= "vorbis",
            audio_bitrate= 192
        ),
    )
}

def to_epoch(datetime_obj):
    """
    Convert a datetime object to an epoch value, as returned by time.time().

    This function supports both Python 2 and Python 3.
    """
    if sys.version_info[0:2] < (3, 3):
        import calendar

        return (
            calendar.timegm(datetime_obj.timetuple())
            + datetime_obj.microsecond / 1000000
        )
    else:
        return datetime_obj.timestamp()

def browser_spoof_open(url):
    return urlopen(
        Request(url, headers={
            "User-agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:25.0) "
                "Gecko/20100101 Firefox/25.0"
            ),
        }),
        timeout= 1
    )

def create_feed_url(username, page_index):
    """
    Create a URL which can be used to a page of video information.

    The maximum number of possible items per page will be used, per YouTube's
    API, and the page_index is zero-based.
    """
    # TODO: Regex for the username? Taking Google+ into account?
    assert isinstance(page_index, int)
    assert page_index >= 0

    return "{}/users/{}/uploads?{}".format(API_URL, username, urlencode((
        # Fetch as JSON.
        ("alt", "json"),
        ("strict", True),
        # Use API version 2
        ("v", 2),
        # 'fields' will constrain the results to include only certain fields.
        ("fields", "entry(id,title,published,media:group(media:description))"),
        ("start-index", page_index * MAX_RESULTS + 1),
        ("max-results", MAX_RESULTS),
    )))

def download_video_feed(feed_url):
    """
    Given a feed URL, download a tuple of FeedItems.
    """
    with urlopen(feed_url) as conn:
        data = json.loads(conn.read().decode())

    return tuple(
        FeedItem(
            # The ID is part of the text of the string.
            video_id= entry["id"]["$t"].rsplit(":", 1)[1],
            # The upload time is an ISO UTC date with milliseconds.
            # The milliseconds are apparently always zero.
            upload_time= datetime.datetime.strptime(
                entry["published"]["$t"],
                "%Y-%m-%dT%H:%M:%S.000Z"
            ),
            title= entry["title"]["$t"],
            description= entry["media$group"]["media$description"]["$t"],
        )
        for entry in data["feed"].get("entry", [])
    )

def create_info_url(video_id):
    assert VIDEO_ID_REGEX.match(video_id)

    return "{}?{}".format(INFO_URL, urlencode((
        ("asv", 3),
        ("el", "detailpage"),
        ("hl", "en_US"),
        ("video_id", video_id),
    )))

def download_info(info_url):
    with browser_spoof_open(info_url) as conn:
        # The video info is a urlencoded string.
        data = parse_qs(conn.read().decode())

    if "errorcode" in data:
        raise RuntimeError("Download failed for {} with reason: {}".format(
            info_url, data.get("reason")
        ))

    # The list of downloads is yet another encoded string.
    stream_map = parse_qs(data["url_encoded_fmt_stream_map"][0])

    # A signature string needs to be added to the download URL.
    # This signature sometimes has ,quality=... after it.
    # This signature is sometimes listed once, other times listed
    # for each entry.

    fallback_host = stream_map["fallback_host"][0]

    def full_url(base_url, sig):
        return "{}&signature={}&fallback_host={}".format(
            base_url, sig.split(",")[0], fallback_host
        )

    return tuple(
        DownloadInfo(
            # The tag sometimes has ,quality= in it.
            ITAG_MAP[int(itag.split(",")[0])],
            full_url(base_url, sig)
        )
        for itag, base_url, sig in
        zip(stream_map["itag"], stream_map["url"], cycle(stream_map["sig"]))
    )

def download_info_for_feed_item(feed_item):
    return download_info(create_info_url(feed_item.video_id))

def highest_quality_audio_video(download_options):
    """
    Select the highest quality audio_video option from a sequence of
    downlaod options.
    """
    def option_key(option):
        """
        Produce a key for sorting a download option by quality.
        """
        return (
            FILETYPE_RATING_DICT[option.media_type.file_type],
            product(option.media_type.resolution),
            option.media_type.video_bitrate
        )

    return sorted((
        option
        for option in download_options
        if option.media_type.has_video and option.media_type.has_audio
    ), key= option_key, reverse= True)[0]

def user_videos(username):
    """
    Generate a list of all videos for a user.
    """
    for page_index in count():
        entry_list = download_video_feed(
            create_feed_url(username, page_index)
        )

        for entry in entry_list:
            yield entry

        if len(entry_list) < MAX_RESULTS:
            break

def base_filename_for_feed_item(feed_item):
    """
    Return a base filename for a YouTube feed item.

    This is returned in the format <epoch>_<video_id>
    """
    return "{}_{}".format(
        int(to_epoch(feed_item.upload_time)),
        feed_item.video_id
    )

def download_feed_item(feed_item, base_directory):
    """
    Download a feed item into a directory.

    Return a pair (video_filename, json_filename) if the item is downloaded,
    otherwise return None if the video has already been downloaded.
    """
    join_path = partial(os.path.join, base_directory)

    base_filename = base_filename_for_feed_item(feed_item)

    json_filename = join_path("{}.json".format(base_filename))

    if os.path.exists(json_filename):
        # Stop here, we already have this video.
        return

    video_info = highest_quality_audio_video(
        download_info_for_feed_item(feed_item)
    )

    video_filename = join_path("{}.{}".format(
        base_filename, video_info.media_type.file_type
    ))

    with browser_spoof_open(video_info.url) as video_conn:
        with open(video_filename, "wb") as out_file:
            shutil.copyfileobj(video_conn, out_file, 1024 * 8)

    with open(json_filename, "w") as out_file:
        json.dump({
            "version": JSON_FORMAT_VERSION,
            "video_info": video_info.to_json(),
            "feed_item": feed_item.to_json(),
        }, out_file)

    return (video_filename, json_filename)

def download_videos_for_user(username, output_directory, log_file= None):
    def log(format_string, *args):
        if log_file is not None:
            log_file.write(format_string.format(*args))
            log_file.write("\n")

    username = username.lower()

    user_directory = os.path.join(output_directory, username)

    if not os.path.exists(user_directory):
        os.mkdir(user_directory)

    log("Downloading videos for username: {}", username)

    for feed_item in user_videos(username):
        while True:
            try:
                feed_result = download_feed_item(feed_item, user_directory)
                break
            except (socket.timeout, HTTPError) as err:
                # This hack sucks, but I can't figure out how to stop
                # the request errors from happening randomly.
                if not isinstance(err, socket.timeout) \
                and err.code != 403 \
                and err.code != 400 \
                and err.code != 503:
                    raise err

                log("Got a request error, sleeping a little...")
                time.sleep(3)

        if feed_result is not None:
            log("Grabbed item {} - {}", feed_item.video_id, feed_item.title)
            log("filename: {}", feed_result[0])
            log("JSON filename: {}", feed_result[1])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: riptube.py <username> [<output_directory>]")

    username = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) >= 3 else "output"

    if not os.path.exists(output_dir):
        os.mkdir(output_dir)

    download_videos_for_user(username, output_dir, log_file= sys.stderr)
