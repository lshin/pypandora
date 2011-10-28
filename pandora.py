import time
from xml.etree import ElementTree
import xml.dom.minidom
from xml.sax.saxutils import escape as xml_escape
from string import Template
import httplib
import urllib
from os.path import join, abspath, dirname, exists
import re
from urlparse import urlsplit
import socket
import logging
import math
from optparse import OptionParser
from tempfile import gettempdir
import struct
from ctypes import c_uint32
from pprint import pprint
import select
import errno
import sys
try: import simplejson as json
except ImportError: import json
from Queue import Queue
from base64 import b64decode, b64encode
import zlib
from random import choice
from webbrowser import open as webopen

try: from urlparse import parse_qsl, parse_qs
except ImportError: from cgi import parse_qsl, parse_qs







THIS_DIR = dirname(abspath(__file__))
TEMPLATE_DIR = join(THIS_DIR, "templates")

music_buffer_size = 20




# settings
settings = {
    'volume': '6',
    'download_music': False,
    'download_directory': '/tmp',
    'last_station': '386046194963576660',
}




def save_setting(key, value):
    """ saves a value persisitently *in the file itself* so that it can be
    used next time pypandora is fired up.  of course there are better ways
    of storing values persistently, but i want to stick with the '1 file'
    idea """
    global settings
    
    logging.info("saving value %r to %r", value, key)
    with open(abspath(__file__), "r") as h: lines = h.read()
    
    
    start = lines.index("settings = {\n")
    end = lines[start:].index("}\n") + start + 2
    
    chunks = [lines[:start], "", lines[end:]]
    
    settings[key] = value
    new_settings = "settings = {\n"
    for k,v in settings.iteritems(): new_settings += "    %r: %r,\n" % (k, v)
    new_settings += "}\n"
    
    chunks[1] = new_settings
    new_contents = "".join(chunks)
    
    with open(abspath(__file__), "w") as h: h.write(new_contents)





class Connection(object):
    """
    Handles all the direct communication to Pandora's servers
    """
    _pandora_protocol_version = 32
    _pandora_host = "www.pandora.com"
    _pandora_port = 80
    _pandora_rpc_path = "/radio/xmlrpc/v%d" % _pandora_protocol_version
    
    _templates = {
        "sync": """
eNqzsa/IzVEoSy0qzszPs1Uy1DNQsrezyU0tychPcU7MyYGx/RJzU+1yM4uT9Yor85Jt9JFEbQoSixJz
i+1s9OEMJP0Afngihg==""",
        "add_feedback": """
eNqdkssKwjAQRX9FSteNgo/NmC4El/6CTJuhhuZRkrT4+caSQl2IravMzdwzhLmB8qnVZiDnpTXnbFds
s5KDpvCw4oJKTfUNNXEfMERbgUJciUSFdQts1ocOHWqfTg4Dqj7eShN4HqSmyOsO2FsDS02WvJ+ID06a
JlK2JQMsyYVQeuZdirWk7r2s/+B83MZCJkfX7H+MraxVhGb0HoBNcgV1XEyN4UTi9CUXNmXKZp/iBbQI
yo4=""",
        "authenticate": """
eNqNj8EKwkAMRH9FSs+N3uP24FX8h2CDDWx2yyZt/XwVtlAPgqdkJvNggv1T42HhYpLTuTl1x6YPqOxj
Hi4U47bfSDlEMefEpaPZR04ud3K+VhNhl8SJCqnVGXChOL9dSR5aF2Vz0gnhoxHqEWr2GzEvkh6hZSWJ
CFX+CU1ktuYy/OZgKwq7n1/FhWTE""",
        "get_playlist": """
eNq1ks8KwjAMxl9Fxs7LvMfuIHj0FSSwOItNO9o49O2t0MG8iDvslH+/j/CRYPcUt5s4Jhv8odo3bdUZ
FNZb6I/k3JyfSdiMjl7OJm0G1lOkQdgrwgLAkSJJKtHgRO6Ru9arqdUKJyUZET41QhlCYb8lSaP1Q1aF
O3uEUv4pyms027nYfqWyXclvi9fXEIV0Yw8/eJjPCYuHeAObkcrC""",
        "get_stations": """
eNpljrEOgzAMRH8FIWbc7iYM3bv0CyzVolHjBMUu4vNJ1SCBOvnOd082jquEZuGsPsWhvfaXdnQobK/0
vFEIu76TsFMjK7V+Ynv8pCIccpwpk2idDhcKn7L10VxnXrjwMiN8PUINoXbPiFr2cSpUenNEqPYPgv0g
HD7eAIijTD8="""
    }

    def __init__(self, debug=False):
        self.debug = debug
        self.rid = "%07dP" % (time.time() % 10000000) # route id
        self.timeoffset = time.time()
        self.token = None
        self.lid = None # listener id
        self.log = logging.getLogger("pandora")

    @staticmethod
    def dump_xml(x):
        """ a convenience function for dumping xml from Pandora's servers """
        #el = xml.dom.minidom.parseString(ElementTree.tostring(x))
        el = xml.dom.minidom.parseString(x)
        return el.toprettyxml(indent="  ")


    def send(self, get_data, body=None):        
        conn = httplib.HTTPConnection("%s:%d" % (self._pandora_host, self._pandora_port))

        headers = {"Content-Type": "text/xml"}

        # pandora has a very specific way that the get params have to be ordered
        # otherwise we'll get a 500 error.  so this orders them correctly.
        ordered = []
        ordered.append(("rid", self.rid))

        if "lid" in get_data:
            ordered.append(("lid", get_data["lid"]))
            del get_data["lid"]

        ordered.append(("method", get_data["method"]))
        del get_data["method"]

        def sort_fn(item):
            k, v = item
            m = re.search("\d+$", k)
            if not m: return k
            else: return int(m.group(0))

        kv = [(k, v) for k,v in get_data.iteritems()]
        kv.sort(key=sort_fn)
        ordered.extend(kv)


        url = "%s?%s" % (self._pandora_rpc_path, urllib.urlencode(ordered))

        self.log.debug("talking to %s", url)

        # debug logging?
        if self.debug:
            debug_logger = logging.getLogger("debug_logger")
            debug_logger.debug("sending data %s" % self.dump_xml(body))

        body = encrypt(body)
        conn.request("POST", url, body, headers)
        resp = conn.getresponse()

        if resp.status != 200: raise Exception(resp.reason)

        ret_data = resp.read()

        # debug logging?
        if self.debug:
            debug_logger = logging.getLogger("debug_logger")
            debug_logger.debug("returned data %s" % self.dump_xml(ret_data))

        conn.close()

        xml = ElementTree.fromstring(ret_data)
        return xml


    def get_template(self, tmpl, params={}):
        tmpl = zlib.decompress(b64decode(self._templates[tmpl].strip().replace("\n", "")))        
        xml = Template(tmpl)
        return xml.substitute(params).strip()


    def sync(self):
        """ synchronizes the times between our clock and pandora's servers by
        recording the timeoffset value, so that for every call made to Pandora,
        we can specify the correct time of their servers in our call """
        
        self.log.info("syncing time")
        get = {"method": "sync"}
        body = self.get_template("sync")
        timestamp = None


        while timestamp is None:
            xml = self.send(get.copy(), body)
            timestamp = xml.find("params/param/value").text
            timestamp = decrypt(timestamp)

            timestamp_chars = []
            for c in timestamp:
                if c.isdigit(): timestamp_chars.append(c)
            timestamp = int(time.time() - int("".join(timestamp_chars)))

        self.timeoffset = timestamp	    
        return True


    def authenticate(self, email, password):
        """ logs us into Pandora.  tries a few times, then fails if it doesn't
        get a listener id """
        self.log.info("logging in with %s...", email)
        get = {"method": "authenticateListener"}


        body = self.get_template("authenticate", {
            "timestamp": int(time.time() - self.timeoffset),
            "email": xml_escape(email),
            "password": xml_escape(password)
        })
        # we use a copy because do some del operations on the dictionary
        # from within send
        xml = self.send(get.copy(), body)
        
        for el in xml.findall("params/param/value/struct/member"):
            children = el.getchildren()
            if children[0].text == "authToken":
                self.token = children[1].text
            elif children[0].text == "listenerId":
                self.lid = children[1].text	

        if self.lid: return True        
        return False




class Account(object):
    def __init__(self, email, password, debug=False):
        self.log = logging.getLogger("account %s" % email)
        self.connection = Connection(debug)        
        self.email = email
        self.password = password
        self._stations = {}
        self.recently_played = []

        self.current_station = None
        
        # this is used just for its fileno() in the case that we have no current
        # song.  this way, the account object can still work in select.select
        # (which needs fileno())
        self._dummy_socket = socket.socket()
        self.login()
        
    def handle_read(self, to_read, to_write, to_err, shared_data):
        if shared_data["music_buffer"].full(): return
        chunk = self.current_song.read()
        
        if chunk: shared_data["music_buffer"].put(chunk)
        # song is done
        elif chunk is False and self.current_song.done_playing:
            shared_data["music_buffer"] = Queue(music_buffer_size)
            self.current_station.next()
        
    def next(self):
        if self.current_station: self.current_station.next()
        
    @property
    def current_song(self):
        return self.current_station.current_song

    def fileno(self):
        if self.current_song: return self.current_song.fileno()
        else: self._dummy_socket.fileno()
            
    def login(self):
        logged_in = False
        for i in xrange(3):
            self.connection.sync()
            if self.connection.authenticate(self.email, self.password):
                logged_in = True
                break
            else:
                self.log.error("failed login (this happens quite a bit), trying again...")
                time.sleep(1)
        if not logged_in: raise Exception, "can't log in.  wrong username or password?"
        self.log.info("logged in")
        
    @property
    def json_data(self):
        data = {}
        data["stations"] = [(id, station.name) for id,station in self.stations.iteritems()]
        data["stations"].sort(key=lambda s: s[1].lower())
        data["current_station"] = getattr(self.current_station, "id", None)
        data["volume"] = settings["volume"]
        return data
            

    @property
    def stations(self):
        if self._stations: return self._stations
        
        self.log.info("fetching stations")
        get = {"method": "getStations", "lid": self.connection.lid}
        body = self.connection.get_template("get_stations", {
            "timestamp": int(time.time() - self.connection.timeoffset),
            "token": self.connection.token
        })
        xml = self.connection.send(get, body)

        fresh_stations = {}
        station_params = {}
        Station._current_id = 0

        for el in xml.findall("params/param/value/array/data/value"):
            for member in el.findall("struct/member"):
                c = member.getchildren()
                station_params[c[0].text] = c[1].text

            station = Station(self, **station_params)
            fresh_stations[station.id] = station


        # remove any stations that pandora says we don't have anymore
        for id, station in self._stations.items():
            if not fresh_stations.get(id): del self._stations[id]

        # add any new stations if they don't already exist
        for id, station in fresh_stations.iteritems():
            self._stations.setdefault(id, station)

        self.log.info("got %d stations", len(self._stations))
        return self._stations




class Station(object):    
    PLAYLIST_LENGTH = 3

    def __init__(self, account, stationId, stationIdToken, stationName, **kwargs):
        self.account = account
        self.id = stationId
        self.token = stationIdToken
        self.name = stationName
        self.current_song = None
        self._playlist = []
        
        self.log = logging.getLogger(repr(self))

    def like(self):
        # normally we might do some logging here, but we let the song object
        # handle it
        self.current_song.like()

    def dislike(self):
        self.current_song.dislike()
        self.next()
    
    def play(self):
        if self.account.current_station and self.account.current_station is not self:
            self.log.info("changing station to %r", self)
            
        self.account.current_station = self
        
        self.playlist.reverse()
        if self.current_song: self.account.recently_played.append(self.current_song)
        self.current_song = self.playlist.pop()
        self.log.info("playing %r", self.current_song)
        self.playlist.reverse()
        self.current_song.play()
            
    def next(self):
        self.play()
    
    @property
    def playlist(self):
        """ a playlist getter.  each call to Pandora's station api returns maybe
        3 songs in the playlist.  so each time we access the playlist, we need
        to see if it's empty.  if it's not, return it, if it is, get more
        songs for the station playlist """

        if len(self._playlist) >= Station.PLAYLIST_LENGTH: return self._playlist

        self.log.info("getting playlist")
        format = "mp3-hifi" # always try to select highest quality sound
        get = {
            "method": "getFragment", "lid": self.account.connection.lid,
            "arg1": self.id, "arg2": 0, "arg3": "", "arg4": "", "arg5": format,
            "arg6": 0, "arg7": 0
        }

        got_playlist = False
        for i in xrange(2):
            body = self.account.connection.get_template("get_playlist", {
                "timestamp": int(time.time() - self.account.connection.timeoffset),
                "token": self.account.connection.token,
                "station_id": self.id,
                "format": format
            })
            xml = self.account.connection.send(get, body)

            song_params = {}

            for el in xml.findall("params/param/value/array/data/value"):
                for member in el.findall("struct/member"):
                    c = member.getchildren()
                    song_params[c[0].text] = c[1].text
                song = Song(self, **song_params)
                self._playlist.append(song)

            if self._playlist:
                got_playlist = True
                break
            else:
                self.log.error("failed to get playlist, trying again times")
                self.account.login()

        if not got_playlist: raise Exception, "can't get playlist!"
        return self._playlist

    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)

    def __str__(self):
        return "%s" % self.name




class Song(object):
    bitrate = 128
    read_chunk_size = 1024
    

    def __init__(self, station, **kwargs):
        self.station = station

        self.__dict__.update(kwargs)
        #pprint(self.__dict__)
        
        self.seed = self.userSeed
        self.id = self.musicId
        self.title = self.songTitle
        self.album = self.albumTitle
        self.artist = self.artistSummary
        
        
        # see if the big version of the album art exists
        if self.artRadio:
            art_url = self.artRadio.replace("130W_130H", "500W_500H")
            art_url_parts = urlsplit(art_url)
            
            test_art = httplib.HTTPConnection(art_url_parts.netloc)
            test_art.request("HEAD", art_url_parts.path)
            if test_art.getresponse().status != 200: art_url = self.artRadio
        else:
            art_url = self.artistArtUrl
        
        self.album_art = art_url


        self.purchase_itunes =  kwargs.get("itunesUrl", "")
        if self.purchase_itunes:
            self.purchase_itunes = urllib.unquote(parse_qsl(self.purchase_itunes)[0][1])

        self.purchase_amazon = kwargs.get("amazonUrl", "")


        try: self.gain = float(fileGain)
        except: self.gain = 0.0

        self.url = self._decrypt_url(self.audioURL)
        self.duration = 0
        self.song_size = None
        self.download_progress = 0

        def format_title(part):
            part = part.lower()
            part = part.replace(" ", "_")
            part = re.sub("\W", "", part)
            part = re.sub("_+", "_", part)
            return part

        self.filename = join(settings["download_directory"], "%s-%s.mp3" % (format_title(self.artist), format_title(self.title)))

        self._stream_gen = None
        self.sock = None
        self.read()        
        self.log = logging.getLogger(repr(self))
        
        
    @property
    def json_data(self):
        return {
            "id": self.id,
            "album_art": self.album_art,
            "title": self.title,
            "album": self.album,
            "artist": self.artist,
            "purchase_itunes": self.purchase_itunes,
            "purchase_amazon": self.purchase_amazon,
            "gain": self.gain,
            "duration": self.duration,
        }
        

    @staticmethod
    def _decrypt_url(url):
        """ decrypts the song url where the song stream can be downloaded. """
        e = url[-48:]
        d = decrypt(e)
        url = url.replace(e, d)
        return url[:-8]
    
    @property
    def position(self):
        if not self.song_size: return 0
        return self.duration * self.download_progress / float(self.song_size)
    
    @property
    def play_progress(self):
        now = time.time()
        return 100 * self.position / self.duration
    
    @property
    def done_playing(self):
        return time.time() - self._started_streaming >= self.duration
    
    @property
    def done_downloading(self):
        return self.download_progress == self.song_size
    
    def read(self):
        if not self._stream_gen: self._stream_gen = self._stream()
        try: data = self._stream_gen.next()
        except StopIteration: return False
        return data
        
    def fileno(self):
        return self.sock.fileno()
    
    
    def stop(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()
        
    
    def play(self):
        self.station.current_song = self
        

    def _stream(self):
        """ a generator which streams some music """  

        # figure out how fast we should download and how long we need to sleep
        # in between reads.  we have to do this so as to not stream to quickly
        # from pandora's servers        
        bytes_per_second = self.bitrate * 125.0
        sleep_amt = Song.read_chunk_size / bytes_per_second
        
        # so we know how short of time we have to sleep to stream perfectly,
        # but we're going to lower it, so we never suffer from
        # a buffer underrun
        sleep_amt *= .8


        split = urlsplit(self.url)
        host = split.netloc
        path = split.path + "?" + split.query



        # this is a little helper function because we might need to reconnect
        # a few times if a read fails.  we'll just pass in the byte_counter
        # to pick back up where we left off
        def reconnect():
            req = """GET %s HTTP/1.0\r\nHost: %s\r\nRange: bytes=%d-\r\nUser-Agent: pypandora\r\nAccept: */*\r\n\r\n"""
            sock = MagicSocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, 80))
            sock.send(req % (path, host, self.download_progress))
            
            # we wait until after we have the headers to switch to non-blocking
            # just because it's easier that way.  in the worst case scenario,
            # pandora's servers hang serving up the headers, causing our app to hang
            headers = sock.read_until("\r\n\r\n", include_last=True)
            headers = headers.strip().split("\r\n")
            headers = dict([h.split(": ") for h in headers[1:]])
            sock.setblocking(0)
            return sock, headers
        
        self.sock, headers = reconnect()
        yield None
        self.log.info("downloading")
        
        # determine the size of the song, and from that, how long the song is
        # in seconds
        self.song_size = int(headers["Content-Length"])
        self.duration = self.song_size / bytes_per_second
        read_amt = self.song_size


        mp3_data = []
        self.download_progress = 0
        self._started_streaming = time.time()
        last_read = 0
        
        # do the actual reading of the data and yielding it.  if we're
        # successful, we yield some bytes, if we would block, yield None,
        while not self.done_downloading:
            
            # check if it's time to read more music yet.  preload the
            # first 128k quickly so songs play immediately
            now = time.time()
            if now - last_read < sleep_amt and self.download_progress > 131072:
                yield None
                continue
            
            # read until the end of the song, but take a break after each read
            # so we can do other stuff
            last_read = now
            chunk = self.sock.read_until(read_amt, break_after_read=True, buf=Song.read_chunk_size)
            
            # got data?  aggregate it and return it
            if chunk:
                self.download_progress += len(chunk)
                mp3_data.append(chunk)
                yield chunk
                
            # disconnected?  do we need to reconnect, or have we read everything
            # and the song is done?
            elif chunk is False:
                if not self.done_downloading:
                    self.log.error("disconnected, reconnecting at byte %d of %d", self.download_progress, self.song_size)
                    self.sock, headers = reconnect()
                    read_amt = int(headers["Content-Length"])
                    continue
                # done!
                else: break
                
            # are we blocking?  this is normal, keep going
            elif chunk is None:
                continue
            
            
            
        if settings["download_music"]:
            self.log.info("saving file to %s", self.filename)
            mp3_data = "".join(mp3_data)
            
            # tag the mp3
            tag = ID3Tag()
            tag.add_id(self.id)
            tag.add_title(self.title)
            tag.add_album(self.album)
            tag.add_artist(self.artist)
            # can't get this working...
            #tag.add_image(self.album_art)
    
            # and write it to the file
            h = open(self.filename, "w")
            h.write(tag.binary() + mp3_data)
            h.close()
        
        

    def new_station(self, station_name):
        """ create a new station from this song """
        raise NotImplementedError

    def _add_feedback(self, like=True):
        """ common method called by both like and dislike """
        conn = self.station.account.connection

        get = {
            "method": "addFeedback",
            "lid":  conn.lid,
            "arg1": self.station.id,
            "arg2": self.id,
            "arg3": self.seed,
            "arg4": 0, "arg5": str(like).lower(), "arg6": "false", "arg7": 1
        }
        body = conn.get_template("add_feedback", {
            "timestamp": int(time.time() - conn.timeoffset),
            "station_id": self.station.id,
            "token": conn.token,
            "music_id": self.id,
            "seed": self.seed,
            "arg4": 0, "arg5": int(like), "arg6": 0, "arg7": 1
        })
        xml = conn.send(get, body)

    def like(self):
        self.log.info("liking")
        self._add_feedback(like=True)

    def dislike(self, **kwargs):
        self.log.info("disliking")
        self._add_feedback(like=False)
        return self.station.next(**kwargs)

    def __repr__(self):
        return "<Song \"%s\" by \"%s\">" % (self.title, self.artist)





class ID3Tag(object):
    def __init__(self):
        self.frames = []

    def add_frame(self, name, data):
        name = name.upper()
        # null byte means latin-1 encoding...
        # see section 4 http://www.id3.org/id3v2.4.0-structure
        header = struct.pack(">4siBB", name, self.sync_encode(len(data)), 0, 0)
        self.frames.append(header + data)

    def add_artist(self, artist):
        self.add_frame("tpe1", "\x00" + artist)

    def add_title(self, title):
        self.add_frame("tit2", "\x00" + title)

    def add_album(self, album):
        self.add_frame("talb", "\x00" + album)

    def add_id(self, id):
        self.add_frame("ufid", "\x00" + id)

    def add_image(self, image_url):
        mime_type = "\x00" + "-->" + "\x00"
        description = "cover image" + "\x00"
        # 3 for cover image
        data = struct.pack(">B5sB12s", 0, mime_type, 3, description)
        data += image_url
        self.add_frame("apic", data)

    def binary(self):
        total_size = sum([len(frame) for frame in self.frames])
        header = struct.pack(">3s2BBi", "ID3", 4, 0, 0, self.sync_encode(total_size))
        return header + "".join(self.frames)

    def add_to_file(self, f):
        h = open(f, "r+b")
        mp3_data = h.read()
        h.truncate(0)
        h.seek(0)
        h.write(self.binary() + mp3_data)
        h.close()

    def sync_decode(self, x):
        x_final = 0x00;
        a = x & 0xff;
        b = (x >> 8) & 0xff;
        c = (x >> 16) & 0xff;
        d = (x >> 24) & 0xff;

        x_final = x_final | a;
        x_final = x_final | (b << 7);
        x_final = x_final | (c << 14);
        x_final = x_final | (d << 21);
        return x_final

    def sync_encode(self, x):
        x_final = 0x00;
        a = x & 0x7f;
        b = (x >> 7) & 0x7f;
        c = (x >> 14) & 0x7f;
        d = (x >> 21) & 0x7f;

        x_final = x_final | a;
        x_final = x_final | (b << 8);
        x_final = x_final | (c << 16);
        x_final = x_final | (d << 24);
        return x_final















def encrypt(input):
    """ encrypts data to be sent to pandora. """
    block_n = len(input) / 8 + 1
    block_input = input
    
    # pad the string with null bytes
    block_input +=  ("\x00" * ((block_n * 4 * 2) - len(block_input)))
    
    block_ptr = 0
    hexmap = "0123456789abcdef"
    str_hex = []
    
    while block_n > 0:
        # byte swap
        l = struct.unpack(">L", block_input[block_ptr:block_ptr+4])[0]
        r = struct.unpack(">L", block_input[block_ptr+4:block_ptr+8])[0]
        
        # encrypt blocks
        for i in xrange(len(out_key_p) - 2):
            l ^= out_key_p[i]
            f = out_key_s[0][(l >> 24) & 0xff] + out_key_s[1][(l >> 16) & 0xff]
            f ^= out_key_s[2][(l >> 8) & 0xff]
            f += out_key_s[3][l & 0xff]
            r ^= f
            
            lrExchange = l
            l = r
            r = lrExchange
            
        # exchange l & r again
        lrExchange = l
        l = r
        r = lrExchange
        r ^= out_key_p[len(out_key_p) - 2]
        l ^= out_key_p[len(out_key_p) - 1]
        
        # swap bytes again...
        l = c_uint32(l).value
        l = struct.pack(">L", l)
        l = struct.unpack("<L", l)[0]
        r = c_uint32(r).value
        r = struct.pack(">L", r)
        r = struct.unpack("<L", r)[0]

        # hex-encode encrypted blocks
        for i in xrange(4):
            str_hex.append(hexmap[(l & 0xf0) >> 4])
            str_hex.append(hexmap[l & 0x0f])
            l >>= 8;
            
        for i in xrange(4):
            str_hex.append(hexmap[(r & 0xf0) >> 4])
            str_hex.append(hexmap[r & 0x0f])
            r >>= 8;
             
        block_n -= 1
        block_ptr += 8
        
    return "".join(str_hex)



def decrypt(input):
    """ decrypts data sent from pandora. """
    output = []
    
    for i in xrange(0, len(input), 16):
        chars = input[i:i+16]

        l = int(chars[:8], 16)
        r = int(chars[8:], 16)

        for j in xrange(len(in_key_p) - 1, 1, -1):
            l ^= in_key_p[j]
            
            f = in_key_s[0][(l >> 24) & 0xff] + in_key_s[1][(l >> 16) & 0xff]
            f ^= in_key_s[2][(l >> 8) & 0xff]
            f += in_key_s[3][l & 0xff]
            r ^= f
            
            # exchange l & r
            lrExchange = l
            l = r
            r = lrExchange
            
        # exchange l & r
        lrExchange = l
        l = r
        r = lrExchange
        r ^= in_key_p[1]
        l ^= in_key_p[0]

        l = struct.pack(">L", c_uint32(l).value)
        r = struct.pack(">L", c_uint32(r).value)
        output.append(l)
        output.append(r)

    return "".join(output)









# pandora encryption/decryption keys
out_key_p = [
    0xD8A1A847, 0xBCDA04F4, 0x54684D7B, 0xCDFD2D53, 0xADAD96BA, 0x83F7C7D2,
    0x97A48912, 0xA9D594AD, 0x6B4F3733, 0x0657C13E, 0xFCAE0687, 0x700858E4,
    0x34601911, 0x2A9DC589, 0xE3D08D11, 0x29B2D6AB, 0xC9657084, 0xFB5B9AF0
]
out_key_s = """
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
GHR5EpVSd30yfGrEYIXOaQ=="""
out_key_s = struct.unpack("1024I", b64decode(out_key_s.replace("\n", "").strip()))
out_key_s = [out_key_s[i:i+256] for i in xrange(0, len(out_key_s), 256)]

in_key_p = [
    0x71207091, 0x64EC5FDF, 0xA519DC17, 0x19146AB7, 0x18DF87E7, 0x98377B97,
    0x032887B7, 0xC7A310D5, 0xA506E589, 0xE97346B9, 0xE3AA5B39, 0x0261BB1D,
    0x466DDC6D, 0xDEF661FF, 0xCD257710, 0xE50A5901, 0x191CFE2E, 0x16AF68DD
]
in_key_s = """
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
vI6RvzC8SHKYm56hNnuOEg=="""
in_key_s = struct.unpack("1024I", b64decode(in_key_s.replace("\n", "").strip()))
in_key_s = [in_key_s[i:i+256] for i in xrange(0, len(in_key_s), 256)]





html_page = """
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"""
html_page = zlib.decompress(b64decode(html_page.replace("\n", "")))










class MagicSocket(socket.socket):
    """ this is a socket subclass that simplifies reading until a specific
    delimeter (for example, end of http headers) and reading until a specific
    amount has been read (for example, reading the body of an http response
    based on the content-length in the http headers).  it gets kind of
    complicated when you add in non-blocking sockets..."""
    
    def __init__(self, *args, **kwargs):
        self.tmp_buffer = ""
        self._read_gen = None
        
        sock = kwargs.get("sock")
        if sock: self._sock = sock
        else:
            self._sock = self
            super(MagicSocket, self).__init__(*args, **kwargs)
            
    def __getattr__(self, name):
        return getattr(self._sock, name)
        
    def read_until(self, *delims, **kwargs):
        if not self._read_gen: self._read_gen = self._read_until(*delims, **kwargs)
        ret =  self._read_gen.next()
        if ret: self._read_gen = None
        return ret
        
    def _read_until(self, *delims, **kwargs):
        buf = kwargs.get("buf", 1024)
        break_after_read = kwargs.get("break_after_read", False)
        include_last = kwargs.get("include_last", False)

        num_bytes = 0
        if len(delims) == 1 and isinstance(delims[0], int):
            num_bytes = delims[0]
            
        read = ""
        cursor = 0
        last_cursor = None
        first_find = None
        
        delims = list(delims)
        delims.reverse()
        current_delim = delims.pop()
        
        def recv(buf):
            read = ""
            if self.tmp_buffer:
                read += self.tmp_buffer[:buf]
                self.tmp_buffer = self.tmp_buffer[buf:]
                buf -= len(read)
                if not buf: return read     
            return read + self._sock.recv(buf)
        
        while True:
            if not num_bytes:
                # search through the data we have for the delimiters
                lread = len(read)
                while cursor < lread and cursor != last_cursor:
                    found = read.find(current_delim, cursor)
                    last_cursor = cursor
                    if found == -1:
                        cursor = lread - len(current_delim)
                    else:
                        if first_find is None: first_find = found
                        cursor = found + len(current_delim)
                        try: current_delim = delims.pop()
                        except IndexError:
                            if first_find == found: first_find = 0
                            
                            if include_last:
                                self.tmp_buffer = read[found+len(current_delim):]
                                yield read[first_find:found+len(current_delim)]
                            else:
                                self.tmp_buffer = read[found:]
                                yield read[first_find:found]
             
                last_cursor = None
            
            
            try: data = recv(buf)
            except socket.error, err:
                if err.errno is errno.EWOULDBLOCK: yield None
                else:
                    yield False
            else:
                if not data: yield False
                if break_after_read: yield data
                
                read += data
                if num_bytes and len(read) >= num_bytes and not break_after_read:
                    self.tmp_buffer = read[num_bytes:]
                    yield read[:num_bytes]














class WebConnection(object):
    timeout = 60
    
    def __init__(self, sock, source, pandora_account):
        self.pandora_account = pandora_account
        self.sock = sock
        self.source = source
        self.local = self.source == "127.0.0.1"
        
        self.headers = None
        self.path = None
        self.params = {}
        self._request_gen = None
        
        self._stream_gen = None
        self.connected = time.time()
        
        
    def handle_read(self, to_read, to_write, to_err, shared_data):
        ret = self.request_read
        if ret:
            to_read.remove(self)
            to_write.add(self)
        elif ret is False:
            to_read.remove(self)
    
    def handle_write(self, to_read, to_write, to_err, shared_data):
        if self.path == "/":
            self.serve_webpage()
            self.close()
            to_write.remove(self)
            to_err.remove(self)
            
        # long-polling requests
        elif self.path == "/events":
            shared_data["long_pollers"].append(self)
            #self.close()
            #to_write.remove(self)
            #to_err.remove(self)
            
        elif self.path == "/account_info":
            self.send_json(self.pandora_account.json_data)
            self.close()
            to_write.remove(self)
            to_err.remove(self)
            
        elif self.path == "/current_song_info":
            self.send_json(self.pandora_account.current_song.json_data)
            self.close()
            to_write.remove(self)
            to_err.remove(self)
           
        elif self.path.startswith("/control/"):            
            command = self.path.replace("/control/", "")
            if command == "next_song":
                shared_data["music_buffer"] = Queue(music_buffer_size)
                self.pandora_account.next()
                
            elif command == "change_station":
                station_id = self.params["station_id"];
                station = self.pandora_account.stations[station_id]
                save_setting("last_station", station.id)
                station.play()
                
            elif command == "volume":
                level = self.params["level"]
                save_setting("volume", level)
            
            self.send_json({"status": True})
            self.close()
            to_write.remove(self)
            to_err.remove(self)
           
        elif self.path == "/m" and self.local:            
            try: chunk = shared_data["music_buffer"].get(False)
            except: return
            
            done = self.stream_music(chunk)
            if done:
                self.close()
                to_write.remove(self)
                to_err.remove(self)
           
        else:
            self.close()
            to_write.remove(self)
            to_err.remove(self)
                   
        
    def fileno(self):
        return self.sock.fileno()
    
    @property
    def request_read(self):
        if not self._request_gen: self._request_gen = self.read_request()
        return self._request_gen.next()
    
    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except: pass
        self.sock.close()
    
    def read_request(self):
        headers = None
        
        while not headers:
            headers = self.sock.read_until("\r\n\r\n", include_last=True)
            if headers is None: yield None
            elif headers is False:
                yield False
                raise StopIteration
        
        headers = headers.strip().split("\r\n")
        headers.reverse()
        get_string = headers.pop()
        headers.reverse()
        
        url = get_string.split()[1]
        url = urlsplit(url)
        
        self.path = url.path
        self.params = dict(parse_qsl(url.query))        
        self.headers = dict([h.split(": ") for h in headers])
        yield True
        
        
    def send_json(self, data):
        data = json.dumps(data)
        self.sock.send("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n" % len(data))
        self.sock.send(data)

    def serve_webpage(self):
        if exists(join(THIS_DIR, "index.html")):
            with open("index.html", "r") as h: page = h.read()
        else: page = html_page
        
        try:
            self.sock.send("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: %s\r\n\r\n" % len(page))
            self.sock.send(page)
        except:
            print "serving webpage", sys.exc_info()
    

    def stream_music(self, music):
        if not self._stream_gen:
            self._stream_gen = self.send_stream(music)
            done = self._stream_gen.next()
        else: done = self._stream_gen.send(music)
        return done            


    def send_stream(self, music):
        self.sock.send("HTTP/1.1 200 OK\r\n\r\n")
        
        while True:
            try: sent = self.sock.send(music)
            except socket.error, e:
                if e.errno == errno.EWOULDBLOCK:
                    pass
                else:
                    break
                
            music = (yield False)   
        yield True
        



        
class PlayerServer(object):
    def __init__(self, pandora_account):
        self.pandora_account = pandora_account
        
        
        # load our previously-saved station
        station = None
        last_station = settings.get("last_station", None)
        if last_station: station = pandora_account.stations.get(last_station, None)
        # ...or play a random one
        if not station:
            station = choice(pandora_account.stations.values())
            save_setting("last_station", station.id)
        station.play()
        
        self.to_read = set([self.pandora_account])
        self.to_write = set()
        self.to_err = set()
        self.callbacks = []
        

    def serve(self, port=7000):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('', port))
        server.listen(100)
        server.setblocking(0)
        
        self.to_read.add(server)
        last_music_read = time.time()
        shared_data = {
            "music_buffer": Queue(music_buffer_size),
            "messages": []
        }
        
        
        while True:
            read, write, err = select.select(
                self.to_read,
                self.to_write,
                self.to_err,
                0
            )
            
            for sock in read:
                if sock is server:
                    conn, addr = server.accept()
                    conn.setblocking(0)
                    
                    conn = WebConnection(MagicSocket(sock=conn), addr[0], self.pandora_account)
                    self.to_read.add(conn)
                    self.to_err.add(conn)
                    
                else:
                     sock.handle_read(self.to_read, self.to_write, self.to_err, shared_data)                    
                    
                    
            for sock in write:
                sock.handle_write(self.to_read, self.to_write, self.to_err, shared_data)


            for cb in self.callbacks: cb()
            time.sleep(.01)
            
            
            
















if __name__ == "__main__":
    logging.basicConfig(
        format="(%(process)d) %(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )


    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-u', "--username", dest="user", help="your Pandora username (your email)")
    parser.add_option('-p', '--password', dest='password', help='your Pandora password')
    parser.add_option('-i', '--import', dest='import_html', action="store_true", default=False, help="Import index.html into pandora.py")
    parser.add_option('-e', '--export', dest='export_html', action="store_true", default=False, help="Export index.html from pandora.py")
    parser.add_option('-d', '--debug', dest='debug', action="store_true", default=False, help='debug XML to/from Pandora')
    options, args = parser.parse_args()
    
    
    # we're importing html to be embedded
    if options.import_html:
        html_file = join(THIS_DIR, "index.html")
        logging.info("importing html from %s", html_file)
        with open(html_file, "r") as h: html = h.read()
        html = b64encode(zlib.compress(html, 9))
        
        # wrap it at 80 characters
        html_chunks = []
        while True:
            chunk = html[:80]
            html = html[80:]
            if not chunk: break
            html_chunks.append(chunk)
        html = "\n".join(html_chunks)
        
        
        with open(abspath(__file__), "r") as h: lines = h.read()
        start_match = "html_page = \"\"\"\n"
        end_match = "\"\"\"\n"
        start = lines.index(start_match)
        end = lines[start+len(start_match):].index(end_match) + start + len(start_match) + len(end_match)
        
        chunks = [lines[:start], start_match + html + end_match, lines[end:]]
        new_contents = "".join(chunks)
        
        with open(abspath(__file__), "w") as h: h.write(new_contents)
        exit()
        
        
    # we're exporting the embedded html into index.html
    if options.export_html:    
        html_file = join(THIS_DIR, "index.html")
        if exists(html_file):
            logging.error("\n\n*** html NOT exported, %s already exists! ***\n\n", html_file)
            exit()
        logging.info("exporting html to %s", html_file)
        with open(html_file, "w") as h: h.write(html_page)
        exit()
        

    if not options.password or not options.user:
        parser.error("Please provide your username and password with -u and -p")

    if options.debug:
        debug_logger = logging.getLogger("debug_logger")
        debug_logger.setLevel(logging.DEBUG)
        lh = logging.FileHandler(join(gettempdir(), "pypandora_debugging.log"))
        lh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        debug_logger.addHandler(lh)

    account = Account(options.user, options.password, debug=options.debug)
    server = PlayerServer(account)
    
    webopen("http://localhost:7000")
    server.serve()
