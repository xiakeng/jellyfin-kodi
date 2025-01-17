# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

#################################################################################################

import os

from kodi_six import xbmc, xbmcvfs
from contextlib import closing

from .objects.obj import Objects
from .helper import translate, api, window, settings, dialog, event, JSONRPC
from .jellyfin import Jellyfin
from .helper import LazyLogger
from .helper.utils import translate_path

#################################################################################################

LOG = LazyLogger(__name__)

#################################################################################################


class Player(xbmc.Player):

    played = {}
    up_next = False

    def __init__(self):
        xbmc.Player.__init__(self)

    def get_playing_file(self):
        try:
            return self.getPlayingFile()
        except Exception as error:
            LOG.exception(error)

    def get_file_info(self, file):
        try:
            return self.played[file]
        except Exception as error:
            LOG.exception(error)

    def is_playing_file(self, file):
        return file in self.played

    def onPlayBackStarted(self):

        ''' We may need to wait for info to be set in kodi monitor.
            Accounts for scenario where Kodi starts playback and exits immediately.
            First, ensure previous playback terminated correctly in Jellyfin.
        '''
        self.stop_playback()
        self.up_next = False
        count = 0
        monitor = xbmc.Monitor()

        try:
            current_file = self.getPlayingFile()
        except Exception:

            while count < 5:
                try:
                    current_file = self.getPlayingFile()
                    count = 0
                    break
                except Exception:
                    count += 1

                if monitor.waitForAbort(1):
                    return
            else:
                LOG.info('Cancel playback report')

                return

        items = window('jellyfin_play.json')
        item = None

        while not items:

            if monitor.waitForAbort(2):
                return

            items = window('jellyfin_play.json')
            count += 1

            if count == 20:
                LOG.info("Could not find jellyfin prop...")

                return

        for item in items:
            if item['Path'] == current_file:
                items.pop(items.index(item))

                break
        else:
            item = items.pop(0)

        window('jellyfin_play.json', items)

        self.set_item(current_file, item)
        data = {
            'QueueableMediaTypes': "Video,Audio",
            'CanSeek': True,
            'ItemId': item['Id'],
            'MediaSourceId': item['MediaSourceId'],
            'PlayMethod': item['PlayMethod'],
            'VolumeLevel': item['Volume'],
            'PositionTicks': int(item['CurrentPosition'] * 10000000),
            'IsPaused': item['Paused'],
            'IsMuted': item['Muted'],
            'PlaySessionId': item['PlaySessionId'],
            'AudioStreamIndex': item['AudioStreamIndex'],
            'SubtitleStreamIndex': item['SubtitleStreamIndex']
        }
        item['Server'].jellyfin.session_playing(data)
        window('jellyfin.skip.%s.bool' % item['Id'], True)

        if monitor.waitForAbort(2):
            return

        if item['PlayOption'] == 'Addon':
            self.set_audio_subs(item['AudioStreamIndex'], item['SubtitleStreamIndex'])

    def set_item(self, file, item):

        ''' Set playback information.
        '''
        try:
            item['Runtime'] = int(item['Runtime'])
        except (TypeError, ValueError):
            try:
                item['Runtime'] = int(self.getTotalTime())
                LOG.info("Runtime is missing, Kodi runtime: %s" % item['Runtime'])
            except Exception:
                item['Runtime'] = 0
                LOG.info("Runtime is missing, Using Zero")

        try:
            seektime = self.getTime()
        except Exception:  # at this point we should be playing and if not then bail out
            return

        result = JSONRPC('Application.GetProperties').execute({'properties': ["volume", "muted"]})
        result = result.get('result', {})
        volume = result.get('volume')
        muted = result.get('muted')

        jelly_client = Jellyfin(item['ServerId']).get_client()
        current_item = jelly_client.jellyfin.get_item(item['Id'])
        edl = []
        
        try:
            edl = self.get_edl_content(current_item['Path'])
        except :
            edl = []

        item.update({
            'File': file,
            'CurrentPosition': item.get('CurrentPosition') or int(seektime),
            'Muted': muted,
            'Volume': volume,
            'Server': jelly_client,
            'Paused': False,
            'EdlContent': edl
        })

        self.played[file] = item
        LOG.info("-->[ play/%s ] %s", item['Id'], item)

        event("ItemEDLLoaded", edl, hexlify=False)

    def get_edl_content(self, filePath):
        edl_content = []
        file_name, file_extension = os.path.splitext(filePath)

        edl_file = file_name + '.edl'
        if not xbmcvfs.exists(edl_file):
            return edl_content
        
        edl_text = ''
        with closing(xbmcvfs.File(edl_file)) as fo:
            edl_text = fo.read()
        edl_lines = edl_text.splitlines()
 
        for line in edl_lines:
            contents = line.split()
            content_len = len(contents)
            if content_len == 2:
                start = float(contents[0])
                action = int(contents[1])
                edl_content.append({
                    'start': start,
                    'end': None,
                    'action': action
                })
            elif content_len == 3:
                start = float(contents[0])
                end = float(contents[1])
                action = int(contents[2])
                edl_content.append({
                    'start': start,
                    'end': end,
                    'action': action
                })
            elif content_len > 0:
                LOG.warning("Invalid edl line")
        
        return edl_content

    def set_audio_subs(self, audio=None, subtitle=None):
        if audio:
            audio = int(audio)
        if subtitle:
            subtitle = int(subtitle)

        ''' Only for after playback started
        '''
        LOG.info("Setting audio: %s subs: %s", audio, subtitle)
        current_file = self.get_playing_file()

        if self.is_playing_file(current_file):

            item = self.get_file_info(current_file)
            mapping = item['SubsMapping']

            if audio and len(self.getAvailableAudioStreams()) > 1:
                self.setAudioStream(audio - 1)

            if subtitle is None or subtitle == -1:
                self.showSubtitles(False)

                return

            tracks = len(self.getAvailableAudioStreams())

            if mapping:
                for index in mapping:

                    if mapping[index] == subtitle:
                        self.setSubtitleStream(int(index))

                        break
                else:
                    self.setSubtitleStream(len(mapping) + subtitle - tracks - 1)
            else:
                self.setSubtitleStream(subtitle - tracks - 1)

    def detect_audio_subs(self, item):

        params = {
            'playerid': 1,
            'properties': ["currentsubtitle", "currentaudiostream", "subtitleenabled"]
        }
        result = JSONRPC('Player.GetProperties').execute(params)
        result = result.get('result')

        try:  # Audio tracks
            audio = result['currentaudiostream']['index']
        except (KeyError, TypeError):
            audio = 0

        try:  # Subtitles tracks
            subs = result['currentsubtitle']['index']
        except (KeyError, TypeError):
            subs = 0

        try:  # If subtitles are enabled
            subs_enabled = result['subtitleenabled']
        except (KeyError, TypeError):
            subs_enabled = False

        item['AudioStreamIndex'] = audio + 1

        if not subs_enabled or not len(self.getAvailableSubtitleStreams()):
            item['SubtitleStreamIndex'] = None

            return

        mapping = item['SubsMapping']
        tracks = len(self.getAvailableAudioStreams())

        if mapping:
            if str(subs) in mapping:
                item['SubtitleStreamIndex'] = mapping[str(subs)]
            else:
                item['SubtitleStreamIndex'] = subs - len(mapping) + tracks + 1
        else:
            item['SubtitleStreamIndex'] = subs + tracks + 1

    def next_up(self):

        item = self.get_file_info(self.get_playing_file())
        objects = Objects()

        if item['Type'] != 'Episode' or not item.get('CurrentEpisode'):
            return

        next_items = item['Server'].jellyfin.get_adjacent_episodes(item['CurrentEpisode']['tvshowid'], item['Id'])

        for index, next_item in enumerate(next_items['Items']):
            if next_item['Id'] == item['Id']:

                try:
                    next_item = next_items['Items'][index + 1]
                except IndexError:
                    LOG.warning("No next up episode.")

                    return

                break
        server_address = item['Server'].auth.get_server_info(item['Server'].auth.server_id)['address']
        API = api.API(next_item, server_address)
        data = objects.map(next_item, "UpNext")
        artwork = API.get_all_artwork(objects.map(next_item, 'ArtworkParent'), True)
        data['art'] = {
            'tvshow.poster': artwork.get('Series.Primary'),
            'tvshow.fanart': None,
            'thumb': artwork.get('Primary')
        }
        if artwork['Backdrop']:
            data['art']['tvshow.fanart'] = artwork['Backdrop'][0]

        next_info = {
            'play_info': {'ItemIds': [data['episodeid']], 'ServerId': item['ServerId'], 'PlayCommand': 'PlayNow'},
            'current_episode': item['CurrentEpisode'],
            'next_episode': data
        }

        outro = 0
        try:
            outro = self.get_outro(item['EdlContent'], int(self.getTotalTime()))
        except :
            outro = 0
            
        if outro > 0:
            next_info['notification_time'] = outro

        LOG.info("--[ next up ] %s", next_info)
        event("upnext_data", next_info, hexlify=True)

    def get_outro(self, edl_content, total_time):
        if len(edl_content) == 0:
            return 0
        
        for edl in edl_content:
            if edl['end'] is not None and abs(total_time - int(edl['end'])) < 2 and (int(edl['action']) == 0 or int(edl['action']) == 3):
                return total_time - int(edl['start'])

        return 0

    def onPlayBackPaused(self):
        current_file = self.get_playing_file()

        if self.is_playing_file(current_file):

            self.get_file_info(current_file)['Paused'] = True
            self.report_playback()
            LOG.debug("-->[ paused ]")

    def onPlayBackResumed(self):
        current_file = self.get_playing_file()

        if self.is_playing_file(current_file):

            self.get_file_info(current_file)['Paused'] = False
            self.report_playback()
            LOG.debug("--<[ paused ]")

    def onPlayBackSeek(self, time, seek_offset):

        ''' Does not seem to work in Leia??
        '''
        if self.is_playing_file(self.get_playing_file()):

            self.report_playback()
            LOG.info("--[ seek ]")

    def report_playback(self, report=True):

        ''' Report playback progress to jellyfin server.
            Check if the user seek.
        '''
        current_file = self.get_playing_file()

        if not self.is_playing_file(current_file):
            return

        item = self.get_file_info(current_file)

        if window('jellyfin.external.bool'):
            return

        if not report:

            previous = item['CurrentPosition']
            item['CurrentPosition'] = int(self.getTime())

            if int(item['CurrentPosition']) == 1:
                return

            try:
                played = float(item['CurrentPosition'] * 10000000) / int(item['Runtime']) * 100
            except ZeroDivisionError:  # Runtime is 0.
                played = 0

            if played > 2.0 and not self.up_next:

                self.up_next = True
                self.next_up()

            if (item['CurrentPosition'] - previous) < 30:

                return

        result = JSONRPC('Application.GetProperties').execute({'properties': ["volume", "muted"]})
        result = result.get('result', {})
        item['Volume'] = result.get('volume')
        item['Muted'] = result.get('muted')
        item['CurrentPosition'] = int(self.getTime())
        self.detect_audio_subs(item)

        data = {
            'QueueableMediaTypes': "Video,Audio",
            'CanSeek': True,
            'ItemId': item['Id'],
            'MediaSourceId': item['MediaSourceId'],
            'PlayMethod': item['PlayMethod'],
            'VolumeLevel': item['Volume'],
            'PositionTicks': int(item['CurrentPosition'] * 10000000),
            'IsPaused': item['Paused'],
            'IsMuted': item['Muted'],
            'PlaySessionId': item['PlaySessionId'],
            'AudioStreamIndex': item['AudioStreamIndex'],
            'SubtitleStreamIndex': item['SubtitleStreamIndex']
        }
        item['Server'].jellyfin.session_progress(data)

    def onPlayBackStopped(self):

        ''' Will be called when user stops playing a file.
        '''
        window('jellyfin_play', clear=True)
        self.stop_playback()
        LOG.info("--<[ playback ]")

    def onPlayBackEnded(self):

        ''' Will be called when kodi stops playing a file.
        '''
        self.stop_playback()
        LOG.info("--<<[ playback ]")

    def stop_playback(self):

        ''' Stop all playback. Check for external player for positionticks.
        '''
        if not self.played:
            return

        LOG.info("Played info: %s", self.played)

        for file in self.played:
            item = self.get_file_info(file)

            window('jellyfin.skip.%s.bool' % item['Id'], True)

            if window('jellyfin.external.bool'):
                window('jellyfin.external', clear=True)

                if int(item['CurrentPosition']) == 1:
                    item['CurrentPosition'] = int(item['Runtime'])

            data = {
                'ItemId': item['Id'],
                'MediaSourceId': item['MediaSourceId'],
                'PositionTicks': int(item['CurrentPosition'] * 10000000),
                'PlaySessionId': item['PlaySessionId']
            }
            item['Server'].jellyfin.session_stop(data)

            if item.get('LiveStreamId'):

                LOG.info("<[ livestream/%s ]", item['LiveStreamId'])
                item['Server'].jellyfin.close_live_stream(item['LiveStreamId'])

            elif item['PlayMethod'] == 'Transcode':

                LOG.info("<[ transcode/%s ]", item['Id'])
                item['Server'].jellyfin.close_transcode(item['DeviceId'], item['PlaySessionId'])

            path = translate_path("special://profile/addon_data/plugin.video.jellyfin/temp/")

            if xbmcvfs.exists(path):
                dirs, files = xbmcvfs.listdir(path)

                for file in files:
                    # Only delete the cached files for the previous play session
                    if item['Id'] in file:
                        xbmcvfs.delete(os.path.join(path, file))

            result = item['Server'].jellyfin.get_item(item['Id']) or {}

            if 'UserData' in result and result['UserData']['Played']:
                delete = False

                if result['Type'] == 'Episode' and settings('deleteTV.bool'):
                    delete = True
                elif result['Type'] == 'Movie' and settings('deleteMovies.bool'):
                    delete = True

                if not settings('offerDelete.bool'):
                    delete = False

                if delete:
                    LOG.info("Offer delete option")

                    if dialog("yesno", translate(30091), translate(33015), autoclose=120000):
                        item['Server'].jellyfin.delete_item(item['Id'])

            window('jellyfin.external_check', clear=True)

        self.played.clear()
