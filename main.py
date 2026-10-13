# -*- coding: utf-8 -*-
"""
Created on Thu May  7 08:35:27 2020

A simple flask/SocketIO for building very simple youtube DJ application that
can be shared by other users

@author: Nathan
@version: 2.7.8 (10/13/2026)
"""
# this variables are passed onto the html templates
appVersion = 'v2.7.11 (10/13/2026)'
bgColor = '#b2b2de' # no longer used but will keep for backward compatibility

import os
import os.path
import sys
import threading
import time
from datetime import datetime, timedelta
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import logging
import pafy
import qrcode
import pickle

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir:
    os.chdir(base_dir)
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    # the application modules live in src/ so the project root stays tidy;
    # config.py and main.py are the only python files kept at the root
    app_code_dir = os.path.join(base_dir, 'src')
    if os.path.isdir(app_code_dir) and app_code_dir not in sys.path:
        sys.path.insert(0, app_code_dir)

from config import app_port, youtubeApiKey, useYoutube, youtubePL
from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO
from trackext import extract_tracks
import acquire
import cdburn

settingsFile = 'data/settings.json'

def loadSettings():
    global youtubeApiKey
    if os.path.isfile(settingsFile):
        try:
            with open(settingsFile, 'r', encoding='utf-8') as f:
                s = json.load(f)
                if 'youtubeApiKey' in s and s['youtubeApiKey'].strip():
                    youtubeApiKey = s['youtubeApiKey'].strip()
                    print("Loaded youtubeApiKey from settings.json:", youtubeApiKey[:6] + "..." if len(youtubeApiKey) > 6 else youtubeApiKey)
        except Exception as e:
            print("Error reading settings.json:", e)

def saveSettings(newApiKey):
    global youtubeApiKey
    youtubeApiKey = newApiKey.strip()
    try:
        s = {}
        if os.path.isfile(settingsFile):
            try:
                with open(settingsFile, 'r', encoding='utf-8') as f:
                    s = json.load(f)
            except Exception:
                s = {}
        s['youtubeApiKey'] = youtubeApiKey
        os.makedirs(os.path.dirname(settingsFile), exist_ok=True)
        with open(settingsFile, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
        print("Saved youtubeApiKey to settings.json")
    except Exception as e:
        print("Error saving settings.json:", e)

# load settings from settings.json if present
loadSettings()

# set the youtube api key. 
pafy.set_api_key(youtubeApiKey)

app = Flask(__name__,
            template_folder=os.path.join(base_dir, 'templates'),
            static_folder=os.path.join(base_dir, 'static'))
app.config['SECRET_KEY'] = 'secret12345#'
# async_mode is pinned deliberately.  gevent is installed in this environment,
# so flask-socketio would auto-select it - but nothing calls
# gevent.monkey.patch_all(), and without that patching a blocking download in
# the worker task freezes the whole event loop: Socket.IO packets and even
# plain HTTP requests then sit unanswered until the job finishes, which made a
# running job look frozen in the UI.  Real threads do not have that problem.
socketio = SocketIO(app, async_mode='threading',
                    ping_interval=25, ping_timeout=60)


class _PollAccessLogFilter(logging.Filter):
    """Hide access-log lines for the endpoint the UI polls for live updates.

    The burner page asks /burner/state for a snapshot every 1-3 seconds, which
    would otherwise add one line per second per open page to the console and
    to nohup.out.  Everything else - including errors and every other request -
    is still logged.
    """

    QUIET_PATHS = ('/burner/state',)

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        return not any(path in message for path in self.QUIET_PATHS)


logging.getLogger('werkzeug').addFilter(_PollAccessLogFilter())

defaultPlayListFile = 'data/playlist.json'
userPlayListFile = 'data/userPlaylist.json'
invalidVideosFile = 'data/invalidVideos.json'
savedQueListFile = 'data/queList.json'
pafyCacheFile = 'data/pafyCache.pkl'

defaultPlayList = dict() # the default playlist for "guest"
userPlayList = dict()
youtubePlayListUrls = dict()
invalidVideosList = list()
pafyCache = dict()

# dictionary to store the saved que list
savedQueList = dict()

# dictionary to store the tracklist for a video
trackListsFile = 'data/tracklists.json'
videoTrackLists = dict()

# dictionary to store the bookmarks for a video
bookmarksFile = 'data/bookmarks.json'
videoBookmarks = dict()

# dictionary to store the extracted track numbers for a video
trackNumbersFile = 'data/tracknums.json'
videoTrackNumbers = dict()

# dictionary to hold tracks being displayed on /playing html page
mixTracks = dict()
mixTracksFile = 'data/mixTrack.json' 

# keys for playlist that holds all the videoIds
mergedPlayListKey = ''

# playlist categories that could not be refreshed from YouTube and are being
# served from the cached data/userPlaylist.json instead
stalePlayListKeys = set()

# keep track of the number of connected users
userCount = 0
player1Video = ''
player2Video = ''

# flag to indicate if the WSGI Socket.IO server is active
serverStarted = False

# keep track of messages when loading a particular youtube playlist. It keys by url
loadingMessages = dict()

# the admin pin used for testing and other things
adminPin = "0001"
        
# save the playlist as json
def savePlayList():    
    with open(defaultPlayListFile, 'w') as fp:
        json.dump(defaultPlayList, fp, indent=2)

# save the youtube playlist as json
def saveUserPlayList():    
    with open(userPlayListFile, 'w') as fp:
        json.dump(userPlayList, fp, indent=2)

# save the saved que list as json
def saveQueList():    
    with open(savedQueListFile, 'w') as fp:
        json.dump(savedQueList, fp, indent=2)

# load the saved que list from file
def loadSavedQueList():
    global savedQueList
    
    if os.path.isfile(savedQueListFile):
        with open(savedQueListFile) as json_file: 
            savedQueList = json.load(json_file)

# funtion to load the invalid video list
def loadInvalidVideosList():
    global invalidVideosList
    
    if os.path.isfile(invalidVideosFile):
        with open(invalidVideosFile) as json_file: 
            invalidVideosList = json.load(json_file)

# function to load the default playlist i.e. guest from file
def loadPlayList():
    global defaultPlayList
    
    if os.path.isfile(defaultPlayListFile):
        with open(defaultPlayListFile) as json_file: 
            defaultPlayList = json.load(json_file)
            #checkPlayList(playList)
    
    # remove any videos in default playlist that are in the invalid video list
    for videoId in list(defaultPlayList.keys()):
        if videoId in invalidVideosList:
            del defaultPlayList[videoId]

# function to load the user playlist from file instead of reading from youtube
def loadBackupUserPlayList():
    global userPlayList, mergedPlayListKey
    
    if os.path.isfile(userPlayListFile):
        with open(userPlayListFile) as json_file: 
            userPlayList = json.load(json_file)

            for playlistKey in userPlayList.keys():
                # we need to store the merge playlist key 
                if 'all merged' in playlistKey:
                    print("Merged Playlist Key:", playlistKey)
                    mergedPlayListKey = playlistKey
                
                youtubePlayListUrls[playlistKey.title()] = 'N/A -- Disk Backup'    

# seed the in-memory playlists from the cached file before refreshing from
# YouTube, so a playlist that fails to load keeps its previous contents
def seedUserPlayListFromCache():
    global userPlayList
    
    if not os.path.isfile(userPlayListFile):
        return 0
    
    try:
        with open(userPlayListFile, 'r', encoding='utf-8') as json_file:
            cached = json.load(json_file)
    except Exception as e:
        print("Could not seed playlists from cache:", e)
        return 0
    
    if not isinstance(cached, dict):
        return 0
    
    seeded = 0
    for key, value in cached.items():
        # the merged playlist is derived from the others, so never seed it
        if 'all merged' in key.lower():
            continue
        if value and key not in userPlayList:
            userPlayList[key] = value
            seeded += 1
    return seeded

# function to load a youtube playlist 
def loadYouTubePlayList(username, url, forQue=False):
    global userPlayList, youtubePlayListUrls
    
    # Bound before the try so the error handler can always report on them.
    playlistKey = username.title()
    username = username.lower()
    
    try:
        is_cached = False
        if url in pafyCache.keys():
            youtubeList = pafyCache[url]
            is_cached = True
        else:
            youtubeList = pafy.get_playlist2(url)
            pafyCache[url] = youtubeList

        # store information about the videos in playlist
        playlist = dict()
        playlistSize = youtubeList._len
        videoCount = 0
        unreadableVideos = 0

        for video in youtubeList:
            if video.videoid in invalidVideosList:
                print("Deleted video:", video.videoid)
                continue

            # A single video that cannot be read must not abort the whole
            # playlist - skip it and carry on with the rest.
            try:
                videoCount += 1
                loadingText = f'Loading {videoCount} / {playlistSize} ...'
                loadingMessages[url] = loadingText

                playlist[video.videoid] = [video.title, video.thumb, video.duration, video.published, username]
                print(loadingText, video.videoid, video.title, video.thumb, video.duration, "\n")
                
                # Yield control cooperatively to let the server handle progress polls (only when active and not cached)
                if serverStarted and not is_cached and (videoCount % 5 == 0):
                    socketio.sleep(0.01)
            except Exception as videoError:
                unreadableVideos += 1
                print("Skipping unreadable video:", getattr(video, 'videoid', '?'), videoError)

        if unreadableVideos:
            print(f"Skipped {unreadableVideos} unreadable video(s) in {playlistKey}.")

        cachedPlaylist = userPlayList.get(username)

        # Never wipe a category with an empty refresh: if YouTube returned
        # nothing usable, keep whatever was seeded from the cache instead.
        if not playlist and cachedPlaylist:
            print(f"YouTube returned no usable videos for {playlistKey}; "
                  f"keeping the {len(cachedPlaylist)} cached entries.")
            stalePlayListKeys.add(playlistKey)
            return

        if cachedPlaylist and len(playlist) < len(cachedPlaylist):
            print(f"Note: {playlistKey} refreshed to {len(playlist)} videos "
                  f"(cached copy had {len(cachedPlaylist)}).")

        userPlayList[username] = playlist
        
        if not forQue:
            youtubePlayListUrls[playlistKey] = url
        
        #checkPlayList(playlist)

        # save pafy cache dictionary as pickle file
        with open(pafyCacheFile, 'wb') as fp:
            pickle.dump(pafyCache, fp)
            print("Pafy Cache Saved ...")

        print("YouTube playlist loaded: " + youtubeList.title + " / " + str(len(playlist)))
    except Exception as e:
        loadingMessages[url] = 'Error Loading YouTube Playlist ...'
        # The playlist could not be fetched at all - if we have cached
        # contents for it they are kept, so record that clearly.
        if userPlayList.get(username):
            stalePlayListKeys.add(playlistKey)
        print("Error loading YouTube playlist ...\n", url, "\n" , e)

# function to load a youtube playlist in a background thread and emit results back to the client
def loadPlaylistInBackground(username, url, clientId, mobile):
    try:
        loadYouTubePlayList(username, url)
        mergeAllPlayList()
        saveUserPlayList()
        
        # Emitting success payload back to this specific client
        html = getHTMLTable(username=username, filter_text="", for_mobile=mobile)
        socketio.emit('my response', {
            'data': 'Load PlayList Done',
            'playListHTML': html,
            'clientId': clientId
        })
    except Exception as e:
        print("Error in loadPlaylistInBackground:", e)
        socketio.emit('my response', {
            'data': 'Error Loading YouTube Playlist ...',
            'playListHTML': '<div>Error Loading Playlist</div>',
            'clientId': clientId
        })

# function to load a youtube playlist and queue all its videos in a background thread
def loadQueuePlaylistInBackground(username, url, clientId, pin):
    try:
        loadYouTubePlayList(username, url, True)
        
        # Emitting success payload back to this specific client
        html = getHTMLTable(username, "", True, False)
        data_list = getQueListData(username)
        socketio.emit('my response', {
            'data': 'Video Qued Done',
            'queListHTML': html,
            'queListData': data_list,
            'clientId': clientId,
            'pin': pin
        })
    except Exception as e:
        print("Error in loadQueuePlaylistInBackground:", e)
        socketio.emit('my response', {
            'data': 'Error Loading YouTube Playlist ...',
            'queListHTML': '<div>Error Loading Queue Playlist</div>',
            'clientId': clientId
        })

# background task to run the SciPy track extractor on the backend
def extractTracksInBackground(videoId, method, min_distance, prominence, adaptive_window, offset, clientId, playerNum, title):
    try:
        print(f"Starting track extraction for {videoId} (client: {clientId})...")
        extract_tracks(
            video_id=videoId,
            delete=False,
            method=method,
            min_distance=min_distance,
            prominence=prominence,
            adaptive_window=adaptive_window,
            offset=offset
        )
        loadTrackNumbers() # Refresh cache from disk
        socketio.emit('my response', {
            'data': 'Extract Track Numbers Done',
            'videoId': videoId,
            'playerNum': playerNum,
            'title': title,
            'trackNumbers': videoTrackNumbers.get(videoId, []),
            'clientId': clientId
        })
        print(f"Track extraction complete for {videoId}.")
    except Exception as e:
        print(f"Error in extractTracksInBackground: {e}")
        socketio.emit('my response', {
            'data': 'Extract Track Numbers Error',
            'message': str(e),
            'clientId': clientId
        })

# save the tracklists database to json file
def saveTrackLists():
    global videoTrackLists
    
    with open(trackListsFile, 'w') as fp:
        json.dump(videoTrackLists, fp, indent=2)

# function to load the tracklists database from file
def loadTrackLists():
    global videoTrackLists
    
    if os.path.isfile(trackListsFile):
        with open(trackListsFile) as json_file: 
            videoTrackLists = json.load(json_file)

# save the bookmarks database to json file
def saveBookmarks():
    global videoBookmarks
    
    with open(bookmarksFile, 'w') as fp:
        json.dump(videoBookmarks, fp, indent=2)

# load the bookmarks database from json file
def loadBookmarks():
    global videoBookmarks
    
    if os.path.isfile(bookmarksFile):
        with open(bookmarksFile) as json_file:
            videoBookmarks = json.load(json_file)

# save the track numbers database to json file
def saveTrackNumbers():
    global videoTrackNumbers
    
    try:
        with open(trackNumbersFile, 'w', encoding='utf-8') as fp:
            json.dump(videoTrackNumbers, fp, indent=2)
        print(f"Saved track numbers to {trackNumbersFile}")
    except Exception as e:
        print(f"Error saving track numbers: {e}")

# load the track numbers database from json file
def loadTrackNumbers():
    global videoTrackNumbers
    
    if os.path.isfile(trackNumbersFile):
        try:
            with open(trackNumbersFile, 'r', encoding='utf-8') as json_file:
                videoTrackNumbers = json.load(json_file)
        except Exception as e:
            print("Error loading tracknums:", e)
            videoTrackNumbers = dict()

# function to merge two youtube playlist
def mergeYouTubePlayList(username, url):
    global defaultPlayList
    
    try:
        username = username.lower()
        
        youtubeList = pafy.get_playlist2(url)
        oldSize = str(len(defaultPlayList))
        
        # go through the youtube playlist and merge any new videos
        for video in youtubeList:
            videoId = video.videoid
            
            if videoId not in defaultPlayList.keys():
                defaultPlayList[videoId] = [video.title, video.thumb, video.duration, video.published, username]
                print("Merge:", video.videoid, video.title, video.thumb, video.duration, "\n")
            else:
                print("Duplicate video Id: ", videoId, "not merged ...")
                
        #checkPlayList(playlist)
        
        # save the playlist
        savePlayList();
        
        print("YouTube playlist merged: " + youtubeList.title + " / " + oldSize + " | " + str(len(defaultPlayList)))
    except Exception as e:
        print("Error loading YouTube playlist ...\n", url, "\n" , e)

# function to merge all the playlist into a single one
def mergeAllPlayList():
    global defaultPlayList, userPlayList, youtubePlayListUrls, mergedPlayListKey
    
    # first delete the current merged playlist
    print("Removing Old Merged Playlist:", mergedPlayListKey)
    if mergedPlayListKey in userPlayList:
        del userPlayList[mergedPlayListKey]
        del youtubePlayListUrls[mergedPlayListKey.title()]
        print("Done ...")
    
    mergePlaylist = dict()
    duplicates = 0
    
    # load the default playlist videos
    for videoId in defaultPlayList.keys():
        if videoId not in mergePlaylist.keys():
            mergePlaylist[videoId] = defaultPlayList[videoId]
        else:
            print("Duplicate video Id: " + videoId)
            duplicates += 1
    
    # load the user playlist videos
    for username in userPlayList.keys():
        playlist = userPlayList.get(username)
        for videoId in playlist.keys():
            if videoId not in mergePlaylist.keys():
                mergePlaylist[videoId] = playlist[videoId]
            else:
                print("Duplicate video Id: " + videoId)
                duplicates += 1
    
    listSize = str(len(mergePlaylist))
    key = 'All Merged (' + listSize + ')'
    mergedPlayListKey = key.lower()
    userPlayList[mergedPlayListKey] = mergePlaylist
    youtubePlayListUrls[key] = 'N/A'
    
    print("\nMerged playlist: Duplicates -> " + str(duplicates) + " | Size -> " + listSize)

# function to copy a user playlist to the que list
def copyPlayListToQue(username, playlistKey, filter_text = ''):
    global userPlayList
    duplicatePlaylist = dict()
    
    if playlistKey in userPlayList.keys():
        playlist = userPlayList.get(playlistKey)
    else:
        playlist = defaultPlayList

    for videoId in playlist.keys():
        videoInfo = playlist[videoId]
        published = cleanDate(videoInfo[3])
       
        title = videoInfo[0] + " (-" + published.split('-')[0] + "-)"
        title = title.replace(',',', ')
        title_lower = title.lower()
        
        if '+' in filter_text:
            # we doing an "and" search so all terms must match
            matches = [s.strip() for s in filter_text.split('+')]
            
            if all(match in title_lower for match in matches):
                duplicatePlaylist[videoId] = videoInfo    
        elif filter_text == "" or filter_text in title_lower:    
            duplicatePlaylist[videoId] = videoInfo

    userPlayList[username] = dict(sorted(duplicatePlaylist.items(), key=lambda item: item[1]))
    print("Playlist duplicated", playlistKey, '->', username, len(duplicatePlaylist), ' videos')

# function to load a users playlist from a file
def loadUserPlayList(username):
    global userPlayList, youtubePlayListUrls
    username = username.lower()
    
    filename = 'likes_' + username + '.json'
    if os.path.isfile(filename):
        with open(filename) as json_file: 
            playlist = json.load(json_file)
            userPlayList[username] = playlist
            #checkPlayList(playlist)
    
    # add it to the youtube playlist urls so we can display it
    playlistKey = username.title()
    youtubePlayListUrls[playlistKey] = "N/A"
    
    print("User playlist loaded: " + str(len(userPlayList)))

# function to load the pickled pafy cache
def loadPafyCache():
    global pafyCache

    if os.path.isfile(pafyCacheFile):
        with open(pafyCacheFile, 'rb') as fp:
            try:
                pafyCache = pickle.load(fp)
                print("Loaded pafy cache file ...")
            except:
                print("An exception occurred loading pafy cache") 

# function to load the mix track videos
def loadMixVideoTracks():
    global mixTracks

    if os.path.isfile(mixTracksFile):
        print("\nLoading mix tracks list ...")
        with open(mixTracksFile) as json_file: 
            mts = json.load(json_file)
            
            # only add none empty list to the dictionary and 
            # with more than 5 min of play time
            for mixId in mts.keys():
                if mts[mixId] and validMixPlayTime(mts[mixId]):
                    mixTracks[mixId] = mts[mixId]

        # now save out to disk after it's been cleaned up
        with open(mixTracksFile, 'w', encoding='utf-8') as f:
            json.dump(mixTracks, f, ensure_ascii=True, indent=4)                     

# function to get the total time for mix tracks and if less than a certain value
# return false
def validMixPlayTime(tracks):
    MIN_AVG_TIME = 45 # average playtime must be more than this to store it

    lastTrack = tracks[-1] # last track as total playtime
    timeString = lastTrack[2]
    
    hours, minutes, seconds = map(int, timeString.split(':'))
    totalSeconds = hours * 3600 + minutes * 60 + seconds
    avgSeconds = int(totalSeconds/len(tracks))

    print("Mix Total Playtime Seconds", totalSeconds, "/", len(tracks), avgSeconds)
    return (avgSeconds >= MIN_AVG_TIME)

# function to check if the playlist has any videos which were deleted
# from youtube
def checkPlayList(videoList):
    global invalidVideosList
    
    print("Skipping video check ...")
    
    for key in videoList: # key is the videoID
        videoInfo = videoList[key]
                
        try:
            print("Video Exist:\t", key, urlopen(videoInfo[1]).getcode())        
        except HTTPError as err:
            print("Video Deleted:\t", key, err.code)
            invalidVideosList.append(key)
    
    # save the invalid video list
    with open(invalidVideosFile, 'w', encoding='utf-8') as f:
        json.dump(invalidVideosList, f, ensure_ascii=False, indent=4)

# remove any videos which are not accessable from a videoList
def cleanPlayList(videoList):
    cleanedList = list()
    
    for videoInfo in videoList: # key is the videoID
        videoId = videoInfo[0]
        if checkVideoExists(videoId):
            cleanedList.append(videoInfo)
        
    return cleanedList

# check to see if a video exists via the YouTube API
def checkVideoExists(videoId):
    '''Check to see if video is playable and embedable'''
    url = 'https://www.googleapis.com/youtube/v3/videos?part=contentDetails&id=' + videoId + '&key=' + youtubeApiKey        
    results = urlopen(url).read()
    output = results.decode('utf-8')
    print(output)
    if '"totalResults": 0' in output:
        print("Invalid Video: " + videoId)
        return False
    else:
        return True
      
# function get sorted list
def sortPlayList(playlist):    
    sorted_d = sorted(playlist.items(), key=lambda x: x[1])    
    return sorted_d

# clean up the publish timestamp. For now just return the year-month-day part
def cleanDate(publishTS):
    return publishTS.split()[0]
    
# function to create an html table consisting of the playList
def getHTMLTable(username = "", filter_text = "", que_list = False, sort = True, for_mobile = False):
    global defaultPlayList, userPlayList, invalidVideosList, youtubePlayListUrls 
    
    if sort:
        sortedList = sortPlayList(defaultPlayList)
    else:
        sortedList = defaultPlayList.items()
    
    if (username != "") and (username in userPlayList):
        print('\nGetting user play list: ' + username)
        userlist = userPlayList[username]
        if sort:
            sortedList = sortPlayList(userlist)
        else:
            sortedList = userlist.items()
    
    print('Generating playlist html...')
    
    # add the buttons for the playlist
    tableHtml = ""
    
    if not que_list:
        videoCount = len(defaultPlayList)
        displayName = 'Default (' + str(videoCount) + ')'

        if for_mobile:
            # add button to go to search
            tableHtml += '<div align="center"><a href="#filter_list"><button> Search List</button></a> '
            
            # add button to clear playlist
            tableHtml += '<input type="button" onclick="clearPlayList()" value="Clear List"><br>'
        else: 
            tableHtml += '<b>YouTube Playlist (<a href="#filter_list"> Search </a>): </b>'
            
        tableHtml += '<br><input type="button" onclick="loadPlayListForUser(\'Guest\', \' \', true,' + str(for_mobile).lower() + ')" value="' + displayName + '"> '
        
        for playlistName in youtubePlayListUrls.keys():
            displayName = playlistName

            # add the number of videos in playlist in not dealing with merged playlist
            if 'All Merged' not in playlistName:
                videoCount = len(userPlayList[playlistName.lower()])
                displayName = playlistName + ' (' + str(videoCount) + ')'

            tableHtml += '<input type="button" onclick="loadPlayListForUser(\'' + playlistName + '\', \' \', true, ' + str(for_mobile).lower() + ')" value="' + displayName + '"> '
        
        if for_mobile:
            tableHtml += '</div>'
        else:    
            # add button to allow moving playlist to que
            tableHtml += '<input type="button" onclick="queSavedPlayList()" value="Q"> '

            # add button to clear playlist
            tableHtml += '<input type="button" onclick="clearPlayList()" value="Clear"> '
    else:
        #sortedList = cleanPlayList(sortedList)
        queListString, totalTime, queListTimesString = getQueListString(sortedList)
        
        qsize = len(sortedList)
        tableHtml += '<b>Qued Video(s): ' + str(qsize) + '</b> '
        tableHtml += '<input type="button" onclick="clearQueList()" value="Clear"> '
        tableHtml += '<input type="button" onclick="reloadQueList()" value="RL"> '
        tableHtml += '<input type="button" onclick="playQueList(\'' + queListString + '\')" value="Play All ( ' + totalTime + ' )"> '
        tableHtml += '<input type="button" id="mixButton" onclick="mixQueList(\'' + queListString + '\',\'' + queListTimesString + '\')" value="Play Mix"> '
        tableHtml += 'Start @ Video: <input type="text" id="startMixAt" name="startMixAt" value="1" size="1"> '
        tableHtml += 'Overlap: <input type="text" id="mixOverlap" name="mixOverlap" value="20" size="2"> Seconds || '
        tableHtml += 'Play Percent: <input type="text" id="mixPlayPercent" name="mixPlayPercent" value="60" size="2"> '
        tableHtml += '<input type="button" onclick="stopMixPlay()" value="Stop Mix"><br>'
        # Check / Burn / Download MP3 live in the action row below the table
        # (static markup in index.html), so they are not rebuilt with it
        tableHtml += '<span id="queMixMessage"><b>Curent Mix Track: 0 / Playtime: 0000 seconds</b></span>'
    
    # add the main table for tracks here
    tableHtml += '<br><br><table cellpadding="2" cellspacing="0" border="0" width="100%">'
    
    i = 1
    for video in sortedList:
        videoId = video[0]
        videoInfo = video[1]
        
        # check to see if this video is in the invalid list
        if videoId in invalidVideosList:
            print("Invalid Video -- Deleted:\t", videoId)
            continue
        
        published = cleanDate(videoInfo[3])
        
        title = videoInfo[0] + " (-" + published.split('-')[0] + "-)"
        title = title.replace(',',', ')
        title_lower = title.lower()
        
        meta_info = videoId + " / " + published
        
        if '+' in filter_text:
            # we doing an "and" search so all terms must match
            matches = [s.strip() for s in filter_text.split('+')]
            
            if all(match in title_lower for match in matches):
                if for_mobile:
                    tableHtml += getHTMLTableRowForMobile(i, videoId, title, videoInfo)
                else:
                    tableHtml += getHTMLTableRow(i, que_list, videoId, title, meta_info, videoInfo)        
                
                i += 1
        elif filter_text == "" or filter_text in title_lower:
            if for_mobile:
                tableHtml += getHTMLTableRowForMobile(i, videoId, title, videoInfo)
            else:
                #print("Adding Row: ", i, videoId, title, meta_info, videoInfo)
                tableHtml += getHTMLTableRow(i, que_list, videoId, title, meta_info, videoInfo)        
            
            i += 1
    
    # check to see if to add the end row tag
    if not tableHtml.endswith('</tr>'):
        tableHtml += '</tr>'
        
    tableHtml += '</table>'
    
    #print(tableHtml)
    return(tableHtml)

# get the row for the html table containing videos
def getHTMLTableRow(i, que_list, videoId, title, meta_info, videoInfo):
    # if odd, add new row tag
    if i % 2 == 0:
        rowHtml = ' '
    else:
        rowHtml = '<tr>'
            
    rowHtml += '<td>'
    rowHtml += '<input type="button" onclick="loadVideoForPlayer(1,\'' + videoId + '\')" value="<"> <br>'
    rowHtml += '<input type="button" onclick="loadVideoForPlayer(2,\'' + videoId + '\')" value=">"> <br>'
    
    if not que_list:
        rowHtml += '<input type="button" onclick="removeVideoFromList(\'' + videoId + '\')" value="X">'
    else:
        rowHtml += '<input type="button" onclick="removeVideoFromQueList(\'' + videoId + '\')" value="X">'
            
    if not que_list:
        rowHtml += '<br><input type="button" onclick="addToQueList(\'' + videoId + '\')" value="Q">'    
                
    rowHtml += '</td>'
    rowHtml += '<td class="video-number-cell" data-video-id="' + videoId + '" data-original-index="' + str(i) + '"><b>' + str(i) + '.</b></td>'
    rowHtml += '<td width="25%"><b>' + title + '</b><br>[' + meta_info + '] <a href="#pageTop"><button type="button" class="arrow-btn" title="Go to Top">&uArr;</button></a> <a href="#filter_list"><button type="button" class="arrow-btn" title="Go to Bottom">&dArr;</button></a> '
    
    # check to see if there is a tracklist for the video
    if videoId in videoTrackLists:
        tracklist = videoTrackLists[videoId]
        tracklistCount = len(tracklist)
        if tracklistCount > 0:
            # clean the title so it can be passed to the javascript function
            cleanTitle = title.replace("'", "")
            cleanTitle = cleanTitle.replace('"', '')
            rowHtml += '<input type="button" onclick="showTrackListDialog(\'' + videoId + '\', \'' + cleanTitle + '\')" value="TL (' + str(tracklistCount) + ')">'
            
    # check to see if there is a bookmark for the video
    if videoId in videoBookmarks:
        bookmarks = videoBookmarks[videoId]
        bookmarksCount = len(bookmarks)
        if bookmarksCount > 0:
            cleanTitle = title.replace("'", "")
            cleanTitle = cleanTitle.replace('"', '')
            rowHtml += ' <input type="button" onclick="showBookmarksDialogNoLinks(\'' + videoId + '\', \'' + cleanTitle + '\')" value="BM (' + str(bookmarksCount) + ')">'
            
    rowHtml += '</b></td>'
    rowHtml += '<td><b>' + videoInfo[2] + '</b></td>'

    if i % 2 == 0:
        rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="120" height="90" onclick="loadVideoForPlayer(2,\'' + videoId + '\')"></td>'
    else:
        rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="120" height="90" onclick="loadVideoForPlayer(1,\'' + videoId + '\')"></td>'

    rowHtml += '<td>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>'
            
    # if even, add close row tag
    if i % 2 == 0:
        rowHtml += '</tr>'
            
    return(rowHtml)   

# function to return row for single colum mobile html table
def getHTMLTableRowForMobile(i, videoId, title, videoInfo):
    thumbnailWidth = 180
    thumbnailHeight = 135

    cleanTitle = title.replace("'", "")
    cleanTitle = cleanTitle.replace('"', '')
    
    rowHtml = '<tr><td align="center">' 
    rowHtml += '<b><span class="video-number-cell" data-video-id="' + videoId + '" data-original-index="' + str(i) + '">' + str(i) + '</span>. ' + title + '<br>[' + videoInfo[2] + '] <a href="#pageTop"><button type="button" class="arrow-btn" title="Go to Top">&uArr;</button></a> <a href="#filter_list"><button type="button" class="arrow-btn" title="Go to Bottom">&dArr;</button></a>'
    
    # check to see if there is a tracklist for the video
    if videoId in videoTrackLists:
        tracklist = videoTrackLists[videoId]
        tracklistCount = len(tracklist)
        if tracklistCount > 0:
            rowHtml += ' <input type="button" onclick="showTrackListDialog(\'' + videoId + '\', \'' + cleanTitle + '\')" value="TL (' + str(tracklistCount) + ')">'
            
    rowHtml += '</b><br>'
    
    rowHtml += '<input type="button" onclick="loadVideoForPlayer(1,\'' + videoId + '\')" value=" < PLY1 "> '

    if i % 2 == 0:
        rowHtml += '<img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="' + str(thumbnailWidth) + '" height="' + str(thumbnailHeight) + '" onclick="loadVideoForPlayer(2,\'' + videoId + '\')">'
    else:
        rowHtml += '<img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="' + str(thumbnailWidth) + '" height="' + str(thumbnailHeight) + '" onclick="loadVideoForPlayer(1,\'' + videoId + '\')">'

    rowHtml += ' <input type="button" onclick="loadVideoForPlayer(2,\'' + videoId + '\')" value=" PLY2 > ">'
    
    rowHtml += '<td></tr>'
            
    return(rowHtml)

#function to return a csv string given a sorted list of videos
def getQueListString(sortedList):
    videoListString = ''
    videoTimeString = ''
    totalSeconds = 0
    
    for video in sortedList:
        videoId = video[0]
        videoInfo = video[1]
        videoTime = videoInfo[2]
        
        pt = datetime.strptime(videoTime,'%H:%M:%S')
        videoSeconds = pt.second + pt.minute*60 + pt.hour*3600
        totalSeconds += videoSeconds
        
        videoListString += videoId + ','
        videoTimeString += str(videoSeconds) + ',' 
    
    # get the total time in hours minutes seconds
    totalTime = str(timedelta(seconds=totalSeconds))
    
    # remove the last "," character
    videoListString = videoListString[:-1]
    videoTimeString = videoTimeString[:-1]

    print("JSON Que List String: ", videoListString, totalTime)
    
    return (videoListString, totalTime, videoTimeString)

#function to return an array given username
def getQueListData(username):
    global userPlayList
    
    print('\nGetting Que Data For: ', username)
    
    # get the playlist for the user
    userList = userPlayList[username]
    
    queData = list();
    
    for video in userList.items():
        videoId = video[0]
        videoInfo = video[1]
        videoTitle = videoInfo[0]
        videoTime = videoInfo[2]
        
        pt = datetime.strptime(videoTime,'%H:%M:%S')
        videoSeconds = pt.second + pt.minute*60 + pt.hour*3600
        
        # concat the values with a <:> seperator so we easily split it
        # might make more sense to just return json array
        queData.append(videoId + '\t' + videoTitle + '\t' + str(videoSeconds))

    # print the size of the que data
    print('Size of que data: ', len(queData))
    return queData
   
# get the title and thumbnail image for the video
# https://stackoverflow.com/questions/59627108/retrieve-youtube-video-title-using-api-python
def getVideoInfo(videoId, username):        
    url = "http://www.youtube.com/watch?v=" + videoId
    # pafy runs yt-dlp with its own options, so it needs the app's cookies and
    # JS runtime handed to it explicitly - otherwise this one lookup goes to
    # YouTube anonymously and gets the "confirm you're not a bot" refusal
    video = pafy.new(url, ydl_opts=acquire.ytdlp_shared_opts())            
    return [video.title, video.thumb, video.duration, video.published, username]
    
# add a video to the default playlist and save it
def addToPlayList(videoId, username):
    global defaultPlayList
    
    defaultPlayList[videoId] = getVideoInfo(videoId, username)
    savePlayList()
    
    return getHTMLTable()

# delete a video from a user playlist or default playlist
def deleteFromPlayList(videoId, username):
    global defaultPlayList, userPlayList, invalidVideosList
    
    # see if it's a user playlist or the default guest list
    videolist = userPlayList.get(username);
    if videolist == None:
        videolist = defaultPlayList
        
    if videoId in videolist:
        videolist.pop(videoId)
        addToInvalidVideoList(videoId)
    
    return getHTMLTable(username)

# add a video to the invalid videos list and save it
def addToInvalidVideoList(videoId):
    global invalidVideosList
    
    invalidVideosList.append(videoId)
    
    # save the invalid video list to json file now
    with open(invalidVideosFile, 'w', encoding='utf-8') as f:
        json.dump(invalidVideosList, f, ensure_ascii=False, indent=4)

# add a video to the queue list for a given user
def addToQueList(videoId, username):
    global userPlayList
    
    if username in userPlayList:
        playlist = userPlayList.get(username);
        
        if videoId not in playlist:
            playlist[videoId] = getVideoInfo(videoId, username)
    else:
       playlist = dict();
       playlist[videoId] = getVideoInfo(videoId, username)
       userPlayList[username] = playlist
    
    return getHTMLTable(username, "", True, False)

# delete a video from the queue list for a given user
def deleteFromQueList(videoId, username):
    global userPlayList
    
    playlist = userPlayList.get(username);
        
    if videoId in playlist:
        playlist.pop(videoId)
    
    return getHTMLTable(username, "", True, False)

# clear the queue list for a given user
def clearQueList(username):
    global userPlayList
    
    if username in userPlayList:
        userPlayList.pop(username)
    
    return ""

# add a queue list to the saved queues database
def addToSavedQueList(username, qname, qlist):
    global savedQueList
    
    key  = username + '@' + qname
    savedQueList[key] = qlist
    saveQueList()
    print("Saved Que List: ", key, " | ", len(qlist))

# filter saved queue list keys to retrieve queues belonging to a given username
def filterSavedQueList(username):
    global savedQueList
    
    filteredQueList = dict()
    
    for key in savedQueList.keys():
        if username in key:
            newKey = key.split('@')[1]
            filteredQueList[newKey] = savedQueList[key]
    
    return filteredQueList

# delete a saved queue list from the saved queues database
def deleteFromSavedQueList(username, qname):
    global savedQueList
    
    key  = username + '@' + qname
    if key in savedQueList:
        savedQueList.pop(key)
        saveQueList()
        print("Deleted Que List: ", key)

# function to return a unique name when saving youtube playlist
def getUniquePlayListName(playListName):
    global userPlayList
    count = 0
    
    if '_' not in playListName:
        playListName = playListName + "_" + str(count)
    
    while playListName in userPlayList:
        count += 1
        playListName = playListName.split('_')[0] + "_" + str(count)
    
    return playListName

# add a tracklist for a video
def addTrackListForVideo(videoId, tracklist):
    global videoTrackLists
    
    if tracklist == "CLEAR":
        videoTrackLists.pop(videoId, "No track list found")
    else:
        tla = tracklist.split("\n")
        videoTrackLists[videoId] = tla
    
    # save the tracklist dictionary
    saveTrackLists()
    
# generate a qrcode for the video and save the image
def createQRCode(videoId):
    ytUrl = "https://www.youtube.com/watch?v=" + videoId 
    
    #Creating an instance of qrcode
    qr = qrcode.QRCode(version = 1, box_size = 10, border = 5)
    qr.add_data(ytUrl)
    qr.make(fit=True)
    
    img = qr.make_image(fill='black', back_color='white')
    imgName = 'static/videoQR_' + videoId + '.png'
    img.save(imgName)
    imgUrl = request.base_url.replace('/socket.io', '') + imgName
    #print('Image URL', imgUrl)
    return imgUrl        

# get the current information for the video being played html.
# TO-DO 11/17/2025 Legacy Code -- Remove later
def getCurrentVideoInfoHTML(videoId, videoInfo):
    #soundBarImg = 'https://i.pinimg.com/originals/31/12/81/31128181420688cf4eda6579ef7dfcc9.gif'
    soundBarImg = 'static/img/bar_vu.gif'

    # get the biggest video thumnail
    #https://stackoverflow.com/questions/16222407/url-of-large-image-of-a-youtube-video
    videoImg = 'https://img.youtube.com/vi/' + videoId + '/0.jpg'

    tableHtml = '<font size="+7">' + videoInfo[0] + ' | ' + videoInfo[2] + '</font><br>'
    tableHtml += '<font size="+3"><span align="center" id="tracklist"></span></font>'
    tableHtml += '<table cellpadding="2" cellspacing="0" border="0" width="100%">'
    tableHtml += '<tr>'
    tableHtml += '<td><font size="+5"><span id="track">Track # 1</span></font></td>'
    tableHtml += '<td><font size="+5"><span id="timer">'
    tableHtml += '&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;'
    tableHtml += '</span></font></td>'
    tableHtml += '</tr>'
    tableHtml += '<tr>'
    tableHtml += '<td style="text-align: center;"><img src="' + videoImg + '" alt="Video Thumbnail" width="360" height="270"><br>' 
    tableHtml += '<img src="' + soundBarImg + '" alt="Soundbar Gif" width="360" height="270">'
    tableHtml += '</td>'

    # show the QR code for video on youtube or playlist here
    tableHtml += '<td bgcolor="white"><div id="qrcode" style="width: 50%; margin: 0 auto;"></div></td>'
    tableHtml += '</tr></table>'
    
    return tableHtml
    
# get the current information for the video being played
def getLiteCurrentVideoInfoHTML(videoId, videoInfo):
    qrImg = createQRCode(videoId)
    videoImg = 'https://img.youtube.com/vi/' + videoId + '/0.jpg'
    
    tableHtml = '<font size="+5">' + videoInfo[0] + ' | ' + videoInfo[2] + '</font><br>'
    tableHtml += '<font size="+3"><span id="track">Track # 1</span> <span id="timer"></span></font><br>'
    tableHtml += '<table cellpadding="2" cellspacing="0" border="0" width="100%">'
    tableHtml += '<tr>'
    tableHtml += '<td style="text-align: center;"><img src="' + videoImg + '" alt="Video Thumbnail" width="360" height="270"></td>' 
    tableHtml += '<td bgcolor="white" style="text-align: center;"><img src="' + qrImg + '" alt="QRCode" width="215" height="215"></td>'
    tableHtml += '</tr></table>'
    
    return tableHtml

# add video track from playing html to playmix dictionary
def addToMixTracksDictionary(jsonData):
    print("\nPlay Mix Track Data:", jsonData)
    clientId = jsonData['clientId']
    videoId = jsonData['videoId']
    track = jsonData['track']
    trackAt = jsonData['trackAt']
    title = jsonData['videoTitle']
    format = jsonData['videoFormat']
    videoUrl = jsonData['videoUrl']
    videoImg = jsonData['videoImg']

    mixTracks[clientId].append([track, videoId, trackAt, title, format, videoUrl, videoImg])

    # save out to json
    with open(mixTracksFile, 'w', encoding='utf-8') as f:
        json.dump(mixTracks, f, ensure_ascii=True, indent=4)

# return the body section for the mix tracks
def getMixTracksBodyHTML(mixId):
    bodyHtml = ''

    if mixId in mixTracks:
        mixVideos = mixTracks[mixId]

        for videoInfo in mixVideos:
            if videoInfo[4] == 'youtube':
                videoUrl = "https://www.youtube.com/watch?v=" + videoInfo[1]
                videoImg = 'https://img.youtube.com/vi/' + videoInfo[1] + '/0.jpg'
            else:
                videoUrl = videoInfo[5]
                videoImg = videoInfo[6]

            bodyHtml += '<div><a href="' + videoUrl + '" target="_blank"><img src="' + videoImg + '" alt="Video Thumbnail" width="480" height="360"></a></div>'
            bodyHtml += '<p style="color:#ffffff;"><b>Track # ' + str(videoInfo[0])  + ' Start @ ' + videoInfo[2] + ' || ' + videoInfo[3]  + '</b></p>'
    else:
        bodyHtml = '<p style="color:#ffffff;">INVALID MIX ID: ' + mixId + '</p>'

    return bodyHtml    

# return basic html with video tracks
def getMixTracksHTML(mixId):
    htmlString = """
        <!DOCTYPE html>
        <html>
        <head>
        <title>Video Mix( MIX_ID )</title>
        <style>
            body {
                background-color: #000000;
            }
        </style>
        </head>
        <body>
            TRACKS
        </body>
        </html>
        """
    # add the title
    htmlString = htmlString.replace('MIX_ID', mixId)
    
    if mixId != 'ALL':
        htmlString = htmlString.replace('TRACKS', getMixTracksBodyHTML(mixId))
    else:
        bodyHtml = ''
        for key in mixTracks.keys():
            trackCount = str(len(mixTracks[key]))

            bodyHtml += '<p style="color:#ffffff;"><b>Mix Tracks For (' + key + '): ' + trackCount + '</b></p>'
            bodyHtml += getMixTracksBodyHTML(key)
            bodyHtml +='<hr style="background-color: #ffffff; height: 4px;">'

        htmlString = htmlString.replace('TRACKS', bodyHtml)    

    return htmlString

# process socket io messages from clients and perform appropriate updates
# True for the common YouTube playlist URL shapes.  Using one helper keeps the
# DJ page, the queue and the burner consistent, and accepts http://, a missing
# "www.", music.youtube.com and watch URLs that carry a list= parameter.
def isYouTubePlaylistUrl(text):
    t = (text or '').strip().lower()
    if not t:
        return False
    return ('list=' in t) or ('youtube.com/playlist' in t)


# ---------------------------------------------------------------------------
# CD burner (/burner) support
#
# The burner is intentionally isolated from the DJ player logic: its socket
# messages all start with "Burner " and are handled by handleBurnerMessage()
# before the normal message dispatch runs, so none of the existing
# "if 'x' in msgTitle" branches can be affected.
# ---------------------------------------------------------------------------

# the project currently open on the burner page (not necessarily saved)
burnerWorking = None

# Identifies this run of the app.  The burner page shows it, so a page that is
# connected to a different instance than the one printing the log is obvious.
BURNER_INSTANCE = '{0}-{1:04x}'.format(os.getpid(),
                                       int.from_bytes(os.urandom(2), 'big'))

# Recent burner log lines.  A job keeps running while its page is throttled or
# disconnected, so the lines are kept here and replayed to it on reconnect.
BURNER_LOG_HISTORY = 200
# how many of those lines travel in a state payload (the page only needs a tail)
BURNER_LOG_REPLAY = 60
burnerLogHistory = []
burnerLogSeq = 0

# Name of the job currently running, or '' when idle.
burnerBusy = ''

# Progress of the running job, so a page that polls (or returns from the
# background) can restore the bar without waiting for the next push.
burnerProgressState = {'current': 0, 'total': 0, 'message': ''}

# short lived cache so the UI can be refreshed without hammering the drive
_burnerDriveCache = {'t': 0, 'data': None}
BURNER_DRIVE_TTL = 10


# Actions that change the working project.  Once a project has been saved, any
# of these writes the change straight back to the store.
BURNER_AUTOSAVE_ACTIONS = (
    'Burner Add Url',
    'Burner Remove Track',
    'Burner Clean Missing',
    'Burner Clear Tracks',
    'Burner Sort Tracks',
    'Burner Move Track',
    'Burner Set Blank',
    'Burner Import Que',
)


def getBurnerWorking():
    global burnerWorking
    if burnerWorking is None:
        burnerWorking = cdburn.new_project('Untitled')
    return burnerWorking


def emitBurner(payload):
    # burner messages are broadcast rather than private: the burner page is a
    # separate client from the DJ page that pushes a queue to it.
    try:
        socketio.emit('my response', payload)
    except Exception as e:
        # never let a send failure take down a background burner job
        print('burner: emit failed:', e)


def burnerLog(message, level=''):
    global burnerLogSeq
    # flush explicitly: when the app is piped to a file or run as a service
    # (like deckcast.service) Python block-buffers stdout and the log lines
    # would otherwise appear much later, or not at all
    print('[burner]', message, flush=True)
    burnerLogSeq += 1
    burnerLogHistory.append({'seq': burnerLogSeq, 'message': str(message),
                             'level': level})
    if len(burnerLogHistory) > BURNER_LOG_HISTORY:
        del burnerLogHistory[0]
    try:
        emitBurner({'data': 'Burner Log', 'message': str(message),
                    'level': level, 'seq': burnerLogSeq})
    except Exception:
        pass


def burnerProgress(current, total, message=''):
    burnerProgressState.update({'current': current, 'total': total,
                                'message': message or ''})
    try:
        emitBurner({'data': 'Burner Progress', 'current': current, 'total': total,
                    'message': message})
    except Exception:
        pass


def burnerIsLocal():
    """True when the request comes from this machine (loopback)."""
    try:
        addr = request.remote_addr or ''
    except Exception:
        # no request context (background job, CLI, tests) - allow
        return True
    return addr in ('127.0.0.1', '::1', 'localhost') or addr.startswith('127.')


def burnerMayUse():
    """Whether this client may use the burner at all.

    The burner drives the machine it runs on, so there is nothing for a remote
    client to do with it - and acquiring audio would let anyone who can reach
    the page fill that machine's disk.  The page and every action in it are
    therefore limited to loopback.  Set "burnerAllowRemote": true in
    data/settings.json to open it up to a trusted network, or reach a remote
    server through an ssh tunnel so the browser talks to 127.0.0.1.
    """
    if burnerIsLocal():
        return True
    try:
        return bool(acquire.load_settings().get('burnerAllowRemote'))
    except Exception:
        return False


def burnerRefusalMessage():
    return ('The CD burner is only available on the machine running DeckCastDJ '
            '- open http://127.0.0.1:{0}/burner there, tunnel the port with ssh, '
            'or set "burnerAllowRemote": true in data/settings.json.'.format(app_port))


# ---------------------------------------------------------------------------
# Que download (MP3)
# ---------------------------------------------------------------------------

# Only one download run at a time: the que is per client but the disk is not.
downloadBusy = ''


def downloadRefusalMessage():
    """Same rule as the burner: this writes files to *this* machine."""
    return ('Downloading is only available on the machine running DeckCastDJ, '
            'because the MP3 files are written to that machine. Open '
            'http://127.0.0.1:{0}/ there, tunnel the port with ssh, or set '
            '"burnerAllowRemote": true in data/settings.json.'.format(app_port))


def emitDownloadStatus(clientId, text, **extra):
    """Report download progress to the client that asked for it.

    The extra fields drive the DJ page's progress card: index/total/percent for
    the bar, stage to say what is happening, and the running counts.
    """
    payload = {
        'data': 'Download Status',
        'clientId': clientId,
        'text': text,
        'running': bool(downloadBusy),
    }
    payload.update(extra)
    socketio.emit('my response', payload)


def _downloadQue(username, clientId):
    """Download every video in a client's que as an MP3, off the request thread."""

    def runner():
        global downloadBusy

        downloadBusy = 'Downloading que'
        print('[download] que download started for {0}'.format(username), flush=True)
        total = 0
        finalStage = 'done'
        counts = {'downloaded': 0, 'skipped': 0, 'failed': 0}
        try:
            que = userPlayList.get(username) or {}
            videoIds = list(que.keys())
            total = len(videoIds)

            if not videoIds:
                summary = 'The que is empty - nothing to download.'
                finalStage = 'empty'
            else:
                done = skipped = 0
                failures = []

                emitDownloadStatus(clientId, 'Preparing {0} video(s) ...'.format(total),
                                   index=0, total=total, percent=0, stage='start',
                                   **counts)

                for index, videoId in enumerate(videoIds, start=1):
                    title = (que.get(videoId) or [videoId])[0]
                    label = '{0} / {1} - {2}'.format(index, total, title)

                    if acquire.mp3_path(videoId):
                        skipped += 1
                        counts['skipped'] = skipped
                        print('[download] {0} - already downloaded'.format(label), flush=True)
                        emitDownloadStatus(clientId, 'Already downloaded {0}'.format(label),
                                           index=index, total=total, percent=100,
                                           stage='skip', **counts)
                        continue

                    print('[download] {0}'.format(label), flush=True)
                    emitDownloadStatus(clientId, 'Downloading {0}'.format(label),
                                       index=index, total=total, percent=0,
                                       stage='download', **counts)

                    # yt-dlp reports whole percents twice a second, which is far
                    # too much for a terminal, so the log gets one line per 10%
                    logged = {'step': -1}

                    def on_progress(percent, status_text, _label=label, _index=index,
                                    _logged=logged):
                        converting = percent >= 100
                        emitDownloadStatus(
                            clientId,
                            '{0} - converting to MP3'.format(_label) if converting
                            else '{0} ({1}%)'.format(_label, percent),
                            index=_index, total=total, percent=percent,
                            stage='convert' if converting else 'download', **counts)
                        step = percent // 10
                        if step > _logged['step'] or converting:
                            _logged['step'] = step
                            print('[download] {0} - {1}%'.format(_label, percent), flush=True)

                    try:
                        acquire.download_mp3(videoId, title, progress=on_progress)
                    except Exception as e:
                        # the terminal gets the full reason, the page a short one
                        print('[download] {0} failed: {1}'.format(videoId, e), flush=True)
                        reason = str(e).splitlines()[0].strip()
                        if len(reason) > 90:
                            reason = reason[:87] + '...'
                        failures.append('{0} - {1}'.format(title, reason))
                        counts['failed'] = len(failures)
                        emitDownloadStatus(clientId, 'Failed {0}: {1}'.format(label, reason),
                                           index=index, total=total, percent=100,
                                           stage='fail', **counts)
                    else:
                        done += 1
                        counts['downloaded'] = done
                        print('[download] {0} - saved'.format(label), flush=True)
                        emitDownloadStatus(clientId, 'Saved {0}'.format(label),
                                           index=index, total=total, percent=100,
                                           stage='saved', **counts)

                summaryParts = []
                if done:
                    summaryParts.append('{0} downloaded'.format(done))
                if skipped:
                    summaryParts.append('{0} already there'.format(skipped))
                if failures:
                    summaryParts.append('{0} failed'.format(len(failures)))
                summary = 'Download MP3 finished: ' + (', '.join(summaryParts) or 'nothing to do') + '.'
                if failures:
                    summary += ' ' + '; '.join(failures[:2])
        except Exception as e:
            print('[download] failed: {0}'.format(e), flush=True)
            summary = 'Download failed: {0}'.format(e)

        # clear the flag first, so the message below reports running=False and
        # the page re-enables its buttons
        downloadBusy = ''
        emitDownloadStatus(clientId, summary[:300], index=total, total=total, percent=100,
                           stage=finalStage, **counts)
        print('[download] {0}'.format(summary), flush=True)

    socketio.start_background_task(runner)


def burnerDriveInfo(force=False, cached_only=False):
    """Detected recorders plus a short media status, cached for a few seconds.

    cached_only serves the progress polls: a real scan shells out to PowerShell
    on Windows and takes ~250ms per call, which is far too slow to run every
    second while a job is reporting progress.
    """
    now = time.time()
    cached = _burnerDriveCache.get('data')
    if not force and cached and (now - _burnerDriveCache.get('t', 0)) < BURNER_DRIVE_TTL:
        return cached
    if cached_only:
        return cached if cached else {
            'devices': [], 'present': False, 'blank': None, 'text': 'checking...',
        }

    devices = []
    media = {'present': False, 'blank': False, 'text': ''}
    try:
        devices = cdburn.list_devices()
    except Exception as e:
        print('burner: device scan failed:', e)
    try:
        media = cdburn.media_status()
    except Exception as e:
        media = {'present': False, 'blank': False, 'text': str(e)}

    info = {
        'devices': devices,
        'present': bool(media.get('present')),
        'blank': media.get('blank'),
        'text': media.get('text', ''),
    }
    _burnerDriveCache['t'] = now
    _burnerDriveCache['data'] = info
    return info


def burnerStatePayload(poll=False):
    """Everything the burner page needs to render itself.

    poll=True is the cheap variant served to the progress polls: it never
    triggers a device scan and is marked so the page does not rebuild its
    whole UI on every poll.
    """
    project = getBurnerWorking()
    cdburn.apply_plan(project)

    saved = {}
    try:
        for name, savedProject in cdburn.list_projects(project.get('username')).items():
            saved[name] = {
                'name': savedProject.get('name'),
                'status': savedProject.get('status'),
                'summary': cdburn.project_summary(savedProject),
            }
    except Exception as e:
        print('burner: could not list saved projects:', e)

    try:
        # matches what Clear CD Cache frees: the whole CD cache, not just the
        # open project's folder
        cacheBytes, cacheFiles = cdburn.cache_usage(None, tier='wav')
    except Exception:
        cacheBytes, cacheFiles = 0, 0

    return {
        'data': 'Burner State',
        'poll': bool(poll),
        'project': project,
        'summary': cdburn.project_summary(project),
        'premastered': cdburn.premastered_count(project),
        'saved': saved,
        'drive': burnerDriveInfo(cached_only=poll),
        'platform': sys.platform,
        'cache': {'bytes': cacheBytes, 'files': cacheFiles},
        'instance': BURNER_INSTANCE,
        'busy': burnerBusy,
        'progress': dict(burnerProgressState),
        'log': burnerLogHistory[-BURNER_LOG_REPLAY:],
        'logSeq': burnerLogSeq,
    }


def emitBurnerState():
    try:
        emitBurner(burnerStatePayload())
    except Exception as e:
        print('burner: state build failed:', e)
        emitBurner({'data': 'Burner Error', 'message': str(e)})


def _burnerBackground(name, fn):
    """Run a slow burner job off the request thread."""
    def runner():
        # the assignment below is inside a nested function, so the global
        # declaration has to live here - one in the enclosing body does not
        # apply to this scope
        global burnerBusy

        # log immediately so the terminal shows that the job actually started
        burnerLog(name + ' ...')
        burnerBusy = name
        emitBurner({'data': 'Burner Busy', 'message': name})
        try:
            fn()
        except Exception as e:
            print('burner: {0} failed: {1}'.format(name, e), flush=True)
            burnerLog('{0} failed: {1}'.format(name, e), 'err')
        finally:
            burnerBusy = ''
            burnerProgressState.update({'current': 0, 'total': 0, 'message': ''})
            # logged (not just emitted) so a page that reconnects sees the job
            # finish in the replayed log too
            burnerLog(name + ' finished')
            emitBurner({'data': 'Burner Idle', 'message': name + ' finished'})
            emitBurnerState()
    socketio.start_background_task(runner)


def _burnerReplan(project):
    """Recompute the disc layout and forget stale premaster paths."""
    cdburn.apply_plan(project)
    for track in project.get('tracks', []):
        track['wavFile'] = None
    return project


def _burnerApplyName(project, name):
    """Adopt a project name typed on the page, before the project is saved.

    The media folder is named after the project, so the name has to reach the
    server before the first download or the WAVs land in ytd/cd/Untitled/ and
    are stranded there when the project is later renamed.
    """
    name = str(name or '').strip()
    if not name or name == project.get('name'):
        return False
    return cdburn.set_project_name(project, name, log=burnerLog)


def _burnerAddUrl(project, source, url):
    trackSource = acquire.get_source(source)
    tracks = trackSource.resolve(url, log=burnerLog)

    existing = set((t.get('source'), t.get('ref')) for t in project.get('tracks', []))
    added = 0
    duplicates = 0
    unknownDurations = 0

    for track in tracks:
        if (track.get('source'), track.get('ref')) in existing:
            duplicates += 1
            continue
        if not track.get('seconds'):
            unknownDurations += 1
        project.setdefault('tracks', []).append(track)
        added += 1

    _burnerReplan(project)

    message = 'Added {0} track(s) from {1}.'.format(added, source)
    if duplicates:
        message += ' {0} were already in the project.'.format(duplicates)
    burnerLog(message, 'ok')
    if unknownDurations:
        burnerLog('{0} track(s) have no known duration yet; they are measured '
                  'when the audio is downloaded.'.format(unknownDurations))


def _burnerAcquire(project):
    """Download the audio and premaster every disc - one button, two stages."""
    _burnerReplan(project)

    def progress(i, n, track, text=None):
        burnerProgress(i, n, text or
                       ('Downloading ' + ((track or {}).get('title') or '')))

    ok, errors = cdburn.acquire_project(project, progress=progress, log=burnerLog)
    for err in errors:
        burnerLog(err, 'err')
    if not ok:
        burnerLog('Some tracks could not be downloaded; premastering the rest.',
                  'err')
    cdburn.save_projects()

    # Converting straight after the download is what lets each downloaded
    # original be deleted as soon as its CD-ready WAV exists.
    _burnerPremaster(project, None)


def _burnerPremaster(project, disc):
    _burnerReplan(project)
    indices = [int(disc)] if disc else [d['index'] for d in project.get('discs', [])]
    for index in indices:
        def progress(i, n, track, text=None, _idx=index):
            burnerProgress(i, n, text or 'Premastering disc {0}: {1}'.format(
                _idx, (track or {}).get('title') or ''))
        ok, errors = cdburn.premaster_disc(project, index, progress=progress,
                                           log=burnerLog)
        for err in errors:
            burnerLog(err, 'err')
        if ok:
            burnerLog('Disc {0} premastered.'.format(index), 'ok')
        else:
            burnerLog('Disc {0} finished with errors.'.format(index), 'err')
    cdburn.save_projects()


def _burnerBurn(project, msg):
    dryRun = bool(msg.get('dryRun'))
    testBurn = bool(msg.get('testBurn'))
    verify = msg.get('verify', True)
    device = msg.get('device') or None

    discs = project.get('discs', [])
    target = None
    if msg.get('disc') not in (None, ''):
        # the page offers every disc, so burning them out of order - or writing
        # the same one twice - is deliberate
        try:
            wanted = int(msg['disc'])
        except (TypeError, ValueError):
            wanted = None
        target = next((d['index'] for d in discs
                       if int(d.get('index', 0)) == wanted), None)
        if target is None:
            burnerLog('Disc {0} is not part of this plan.'.format(msg['disc']), 'err')
            return
    if target is None:
        for disc in discs:
            if not disc.get('burnedAt'):
                target = disc['index']
                break
    if target is None and discs:
        target = discs[0]['index']
    if target is None:
        burnerLog('Nothing to burn - no discs planned yet.', 'err')
        return

    result = cdburn.burn_disc(project, target, dry_run=dryRun, verify=verify,
                              device=device, log=burnerLog, progress=burnerProgress,
                              test_burn=testBurn)
    burnerLog('Disc {0}: {1}'.format(target, result.get('message', '')),
              'ok' if result.get('success') else 'err')
    if result.get('command'):
        burnerLog('  command: ' + ' '.join(str(c) for c in result['command']))
    # everything the burner printed was already streamed through the log above
    if not result.get('streamed'):
        for key in ('stdout', 'stderr', 'output'):
            if result.get(key):
                burnerLog(result[key].strip(), 'err' if key == 'stderr' else '')

    # a test burn writes nothing, so the disc is not burned and its audio is
    # not cleared
    if result.get('success') and not result.get('dryRun') and not testBurn:
        for disc in discs:
            if int(disc.get('index', 0)) == int(target):
                disc['burnedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        if all(d.get('burnedAt') for d in discs):
            project['status'] = 'burned'

        if msg.get('autoClean'):
            tier = msg.get('cleanTier') or 'wav'
            # only this disc's audio: the rest of the set is still waiting
            freed, paths, errors = cdburn.clean(project, tier=tier, dry_run=False,
                                                disc=target, log=burnerLog)
            burnerLog('Auto-cleaned {0} file(s), {1:.1f} MB from disc {2}.'.format(
                len(paths), freed / 1048576.0, target), 'ok')
            for err in errors:
                burnerLog(err, 'err')
    cdburn.save_projects()


def _burnerClean(project, msg):
    dryRun = bool(msg.get('dryRun'))
    tier = msg.get('tier') or 'wav'
    # the button clears the whole CD cache: a working project that has been
    # renamed (or never named) would otherwise leave other projects' media on
    # the disk, folders and all
    freed, paths, errors = cdburn.clean(project, tier=tier, dry_run=dryRun,
                                        log=burnerLog, all_projects=True)
    burnerLog('{0} {1} file(s), {2:.1f} MB from the CD cache.'.format(
        'Would free' if dryRun else 'Freed', len(paths), freed / 1048576.0),
        'ok' if not errors else 'err')
    for err in errors:
        burnerLog(err, 'err')


def _burnerImportQue(project, msg):
    """Accept a queue pushed here by the Burn button on another page."""
    source = str(msg.get('source') or 'youtube').lower()
    origin = msg.get('origin') or 'another page'
    incoming = msg.get('tracks')

    existing = set((t.get('source'), t.get('ref')) for t in project.get('tracks', []))
    added = 0

    if incoming:
        # the sender already knew the titles/durations (mp3 page)
        for item in incoming:
            ref = str(item.get('ref') or '').strip()
            if not ref or (source, ref) in existing:
                continue
            project.setdefault('tracks', []).append({
                'source': source,
                'ref': ref,
                'title': item.get('title') or ref,
                'artist': item.get('artist') or '',
                'seconds': int(item.get('seconds') or 0),
                'bitrate': int(item.get('bitrate') or 0),
                'srcFile': None,
            })
            existing.add((source, ref))
            added += 1
    else:
        # the DJ page queues by client id; reuse the titles/times it already has
        queKey = str(msg.get('clientId') or msg.get('username') or '').lower().strip()
        try:
            rows = getQueListData(queKey)
        except Exception as e:
            burnerLog('Could not read the DJ queue ({0}): {1}'.format(queKey, e), 'err')
            rows = []
        for row in rows:
            parts = row.split('\t')
            if len(parts) < 3:
                continue
            videoId, title, seconds = parts[0], parts[1], parts[2]
            if ('youtube', videoId) in existing:
                continue
            project.setdefault('tracks', []).append({
                'source': 'youtube',
                'ref': videoId,
                'title': title,
                'artist': '',
                'seconds': int(float(seconds)),
                'bitrate': 0,
                'srcFile': None,
            })
            existing.add(('youtube', videoId))
            added += 1

    _burnerReplan(project)
    emitBurner({'data': 'Burner Imported', 'count': added, 'origin': origin})
    burnerLog('Imported {0} track(s) from {1}.'.format(added, origin), 'ok')
    emitBurnerState()


def handleBurnerMessage(msg):
    """Handle any message whose data field starts with 'Burner '."""
    global burnerWorking

    action = str(msg.get('data', ''))

    if not burnerMayUse():
        # Not written to the burner log on purpose: a remote caller must not be
        # able to write into the local page's activity log.
        message = burnerRefusalMessage()
        print('[burner] refused {0} from a remote client'.format(action), flush=True)
        try:
            socketio.emit('my response', {'data': 'Burner Error', 'message': message},
                          room=request.sid)
        except Exception:
            pass
        return

    project = getBurnerWorking()

    if action == 'Burner Get State':
        emitBurnerState()

    elif action == 'Burner Refresh Drive':
        burnerDriveInfo(force=True)
        emitBurnerState()

    elif action == 'Burner Add Url':
        url = str(msg.get('url') or '').strip()
        source = str(msg.get('source') or 'youtube').lower()
        if url:
            _burnerBackground('Looking up ' + source,
                              lambda: _burnerAddUrl(project, source, url))

    elif action == 'Burner Remove Track':
        try:
            index = int(msg.get('index', -1))
        except (TypeError, ValueError):
            index = -1
        tracks = project.get('tracks', [])
        if 0 <= index < len(tracks):
            before = cdburn.premaster_paths(project)
            tracks.pop(index)
            _burnerReplan(project)
            cdburn.carry_premasters(project, before, log=burnerLog)
        emitBurnerState()

    elif action == 'Burner Clean Missing':
        # a refused download (copyright, region lock) leaves a track that can
        # never be burned, so the button on the Tracks panel clears them out
        if not cdburn.remove_unpremastered(project, log=burnerLog):
            burnerLog('Every track has its audio - nothing to remove.', 'ok')
        _burnerReplan(project)
        emitBurnerState()

    elif action == 'Burner Clear Tracks':
        project['tracks'] = []
        _burnerReplan(project)
        emitBurnerState()

    elif action == 'Burner Sort Tracks':
        before = cdburn.premaster_paths(project)
        project['tracks'] = sorted(project.get('tracks', []),
                                   key=lambda t: (t.get('title') or '').lower())
        _burnerReplan(project)
        cdburn.carry_premasters(project, before, log=burnerLog)
        emitBurnerState()

    elif action == 'Burner Move Track':
        try:
            index = int(msg.get('index'))
            delta = -1 if int(msg.get('delta', 1)) < 0 else 1
        except (TypeError, ValueError):
            emitBurnerState()
            return
        if cdburn.move_track(project, index, delta, log=burnerLog):
            _burnerReplan(project)
            emitBurnerState()

    elif action == 'Burner Set Blank':
        before = cdburn.premaster_paths(project)
        try:
            project['blank'] = int(msg.get('blank') or cdburn.DEFAULT_BLANK)
        except (TypeError, ValueError):
            project['blank'] = cdburn.DEFAULT_BLANK
        _burnerReplan(project)
        cdburn.carry_premasters(project, before, log=burnerLog)
        emitBurnerState()

    elif action == 'Burner New Project':
        burnerWorking = cdburn.new_project('Untitled')
        emitBurnerState()

    elif action == 'Burner Set Name':
        # the name is pushed as it is typed, so downloads started before the
        # project is saved still go to the folder the user asked for
        name = str(msg.get('name') or '').strip()
        if not name or name == project.get('name'):
            pass
        elif burnerBusy:
            burnerLog('Rename to "{0}" ignored while "{1}" is running.'.format(
                name, burnerBusy), 'err')
        else:
            old_name = project.get('name')
            _burnerApplyName(project, name)
            # a renamed project keeps its saved record, under the new name
            cdburn.autosave_project(project, previous_name=old_name)
            emitBurnerState()

    elif action == 'Burner Save Project':
        name = str(msg.get('name') or '').strip()
        if name:
            old_name = project.get('name')
            username = str(msg.get('username') or project.get('username') or 'guest').lower().strip()
            _burnerApplyName(project, name)
            project['username'] = username
            _burnerReplan(project)
            cdburn.upsert_project(cdburn.project_key(username, name), project)
            if old_name and old_name != name:
                # a renamed project must not stay listed under its old name
                old_key = cdburn.project_key(username, old_name)
                if cdburn.get_project(old_key) is project:
                    cdburn.delete_project(old_key)
            burnerLog('Saved project "{0}".'.format(name), 'ok')
        emitBurnerState()

    elif action == 'Burner Load Project':
        name = str(msg.get('name') or '').strip()
        found = cdburn.get_project(cdburn.project_key(project.get('username'), name))
        if found:
            burnerWorking = found
            _burnerReplan(burnerWorking)
            burnerLog('Loaded project "{0}".'.format(name), 'ok')
        else:
            burnerLog('No saved project named "{0}".'.format(name), 'err')
        emitBurnerState()

    elif action == 'Burner Delete Project':
        name = str(msg.get('name') or '').strip()
        if cdburn.delete_project(cdburn.project_key(project.get('username'), name)):
            burnerLog('Deleted project "{0}".'.format(name), 'ok')
        emitBurnerState()

    elif action == 'Burner Acquire':
        # the page sends the name box along, so a rename cannot be missed
        _burnerApplyName(project, msg.get('name'))
        _burnerBackground('Acquiring audio and premastering',
                          lambda: _burnerAcquire(project))

    elif action == 'Burner Burn':
        _burnerBackground('Burning', lambda: _burnerBurn(project, msg))

    elif action == 'Burner Clean':
        _burnerBackground('Cleaning cache', lambda: _burnerClean(project, msg))

    elif action == 'Burner Import Que':
        _burnerImportQue(project, msg)

    else:
        print('burner: unhandled message:', action)
        return

    # A project the user has already saved follows every change made on the
    # page, so Load (or a restart) brings back the tracks that are on screen.
    if action in BURNER_AUTOSAVE_ACTIONS:
        cdburn.autosave_project(project)


def processMessage(json):
    global defaultPlayList, userCount, player1Video, player2Video
    
    #print("Processing msg: " + str(json));
    msgTitle = json['data']
    
    # update the number of connected users
    if 'User Connected' in msgTitle:
        userCount += 1
        json['userCount'] = userCount
        
        if player1Video:
            json['videoId1'] = player1Video
        
        if player2Video:
            json['videoId2'] = player2Video
    
    # if we loaded a playlist indicate as much
    if 'Load PlayList' in msgTitle:
        playListUser = json['uname'].lower().strip()
        f_text = json['filter'].strip()
        mobile = json['mobile']
        clientId = json['clientId']
        
        # check to see if to to load a youtube playlist
        if isYouTubePlaylistUrl(f_text):
            playListUser = getUniquePlayListName(playListUser)
            socketio.start_background_task(
                loadPlaylistInBackground, playListUser, f_text, clientId, mobile
            )
        else:
            json['playListHTML'] = getHTMLTable(username = playListUser, filter_text = f_text.lower(), for_mobile = mobile)
    
    # merge a youtube playlist to the save playlist
    if 'Merge PlayList' in msgTitle:
        username = json['uname'].lower().strip()
        f_text = json['filter'].strip()
        
        # check to see if to to load a youtube playlist
        if (username == "guest0") and isYouTubePlaylistUrl(f_text):
            username = 'guest';
            mergeYouTubePlayList(username, f_text)
            f_text = ""
            
            json['playListHTML'] = getHTMLTable(username = username, filter_text = f_text.lower())
    
    # see if to update and return the playlist html
    if 'Video Changed' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
        
        if json['player'] == 1:
            player1Video = videoId
        else:
            player2Video = videoId
            
    # see if to add the video to the que list for the client
    if 'Video Qued' in msgTitle:
        videoId = json['videoId']
        username = json['clientId'].lower().strip()
        clientId = json['clientId']
        pin = json['pin']
        
        # check to see if to to load a youtube playlist and add to que
        if isYouTubePlaylistUrl(videoId):
            socketio.start_background_task(
                loadQueuePlaylistInBackground, username, videoId, clientId, pin
            )
        elif 'savedList:' in videoId:
            savedList = videoId.split(":")[1]
            f_text = json['filter'].strip().lower()

            copyPlayListToQue(username.lower(), savedList.lower(), f_text)
            json['queListHTML'] = getHTMLTable(username, "", True, False)
            json['queListData'] = getQueListData(username)
        elif videoId == "-1":
            # just reload the que list
            json['queListHTML'] = getHTMLTable(username, "", True, False)
            json['queListData'] = getQueListData(username)
        elif videoId != "0":
            json['queListHTML'] = addToQueList(videoId, username)
            json['queListData'] = getQueListData(username)
        else:
            # videoId = 0 so clear cue list
            json['queListHTML'] = clearQueList(username)
    
    # download the que as MP3 files - local machine only, like the burner,
    # because the files land on whatever machine runs the app
    if 'Download Que' in msgTitle:
        username = str(json.get('clientId') or '').lower().strip()
        clientId = json.get('clientId')

        if not burnerMayUse():
            print('[download] refused Download Que from a remote client', flush=True)
            # private to the caller: a remote client must not be able to write
            # into the local page's console line
            socketio.emit('my response', {
                'data': 'Download Status',
                'clientId': clientId,
                'text': downloadRefusalMessage(),
                'running': False,
                'refused': True,
            }, room=request.sid)
        elif downloadBusy:
            emitDownloadStatus(clientId, 'A download is already running - wait for it '
                                         'to finish.')
        else:
            _downloadQue(username, clientId)

    # see if to save the video to the playlist
    if 'Video Saved' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
                
        # only save videos for the secret guest0 to prevent anyone from making a 
        # mess of the playlist. A better way would be to use seperate pin variable
        if username == "guest0":
            json['videoSaved'] = True
            
            if videoId not in defaultPlayList:
                json['playListHTML'] = addToPlayList(json['videoId'], "guest")
                json['savedVideo'] = "Video Saved To Playlist ..."
            else:
                json['savedVideo'] = "Video Already in Playlist ..."
    
    # see if to broadcast the video that's currently playing
    if 'Current Video' in msgTitle:
        videoId = json['videoId']
        pin = json['pin']
        # clientId = json['clientId'].lower().strip()
                
        # return information on current video
        videoInfo = getVideoInfo(videoId, "N/A")
        pt = datetime.strptime(videoInfo[2], '%H:%M:%S')
        totalSeconds = pt.second + pt.minute*60 + pt.hour*3600
        
        json['videoTitle'] = videoInfo[0] + ' | ' + videoInfo[2]
        #json['videoInfoHTML'] = getCurrentVideoInfoHTML(videoId, videoInfo)
        json['videoThumbnail'] = 'https://img.youtube.com/vi/' + videoId + '/0.jpg'
        json['backupThumbnail'] = 'https://upload.wikimedia.org/wikipedia/commons/8/8b/YouTube_dark_icon_%282017%29.svg'
        json['videoInfoLiteHTML'] = getLiteCurrentVideoInfoHTML(videoId, videoInfo)
        json['videoTime'] = totalSeconds
        json['videoFormat'] = 'youtube'
        json['videoUrl'] = ''
        json['videoImg'] = ''

        if videoId in videoTrackLists:
            json['trackList'] = videoTrackLists[videoId]
        elif videoId in videoTrackNumbers:
            json['trackList'] = videoTrackNumbers[videoId]
        else:
            json['trackList'] = []
        json['rawTrackList'] = videoTrackLists.get(videoId, [])
        json['trackNumbers'] = videoTrackNumbers.get(videoId, [])

    # see if to add the tracklist for a particular video
    if 'Add TrackList' in msgTitle:
        videoId = json['videoId']
        pin = json['pin']
        tracklist = json['tracklist'].strip()

        print("Adding/Editing TrackList", tracklist)

        # only add tracklist for admin pin
        if pin == adminPin:
            if len(tracklist) != 0:
                addTrackListForVideo(videoId, tracklist)
        
    # see if to edit or add the tracklist
    if 'Edit TrackList' in msgTitle:
        videoId = json['videoId']

        if videoId in videoTrackLists:
            json['trackList'] = videoTrackLists[videoId]
        else:
            json['trackList'] = []

    # see if to get only the tracklist for a video
    if 'Get TrackList Only' in msgTitle:
        videoId = json['videoId']
        if videoId in videoTrackLists:
            json['trackList'] = videoTrackLists[videoId]
        elif videoId in videoTrackNumbers:
            json['trackList'] = videoTrackNumbers[videoId]
        else:
            json['trackList'] = []
        json['rawTrackList'] = videoTrackLists.get(videoId, [])
        json['trackNumbers'] = videoTrackNumbers.get(videoId, [])

    # see if to view bookmarks for a video
    if 'View Bookmarks' in msgTitle:
        videoId = json['videoId']
        json['bookmarks'] = videoBookmarks.get(videoId, [])

    # see if to add a bookmark
    if 'Add Bookmark' in msgTitle:
        videoId = json['videoId']
        time = float(json['time'])
        uname = json['uname'].strip() or "Guest"
        
        if videoId not in videoBookmarks:
            videoBookmarks[videoId] = []
            
        # check if exact/very close time already bookmarked
        duplicate = False
        for bm in videoBookmarks[videoId]:
            if abs(bm[0] - time) < 0.1:
                duplicate = True
                break
                
        if not duplicate:
            # Use custom description if provided, otherwise default fallback
            desc_val = json.get('desc') or f"Bookmark by {uname}"
            # append as sublist: [time, description]
            videoBookmarks[videoId].append([round(time, 2), desc_val])
            # sort chronologically by time
            videoBookmarks[videoId].sort(key=lambda x: x[0])
            saveBookmarks()
            
        json['bookmarks'] = videoBookmarks[videoId]
        if not json.get('silent'):
            # Redirect data to View Bookmarks so it opens the dialog
            json['data'] = 'View Bookmarks'
        else:
            json['data'] = 'Add Bookmark Silent Done'

    # see if to edit a single bookmark's description
    if 'Edit Bookmark' in msgTitle:
        videoId = json['videoId']
        time = float(json['time'])
        desc = json['desc']
        
        if videoId in videoBookmarks:
            for bm in videoBookmarks[videoId]:
                if abs(bm[0] - time) < 0.1:
                    bm[1] = desc
                    break
            saveBookmarks()

    # see if to delete a single bookmark
    if 'Delete Bookmark' in msgTitle:
        videoId = json['videoId']
        time = float(json['time'])
        
        if videoId in videoBookmarks:
            videoBookmarks[videoId] = [bm for bm in videoBookmarks[videoId] if abs(bm[0] - time) >= 0.1]
            saveBookmarks()

    # see if to clear all bookmarks for a video
    if 'Clear Bookmarks' in msgTitle:
        videoId = json['videoId']
        
        if videoId in videoBookmarks:
            videoBookmarks[videoId] = []
            saveBookmarks()

    # see if to view track numbers for a video
    if 'View Track Numbers' in msgTitle:
        videoId = json['videoId']
        # Make sure our database cache matches disk
        loadTrackNumbers()
        json['trackNumbers'] = videoTrackNumbers.get(videoId, [])

    # see if to trigger track numbers extraction
    if 'Extract Track Numbers' in msgTitle:
        videoId = json['videoId']
        method = json.get('method', 'adaptive')
        min_distance = int(json.get('min_distance', 45))
        prominence = float(json.get('prominence', 0.035))
        adaptive_window = int(json.get('adaptive_window', 150))
        offset = float(json.get('offset', 0.02))
        clientId = json['clientId']
        playerNum = json['playerNum']
        title = json['title']

        socketio.start_background_task(
            extractTracksInBackground,
            videoId, method, min_distance, prominence, adaptive_window, offset,
            clientId, playerNum, title
        )

    # see if to clear/delete track numbers for a video
    if 'Delete Track Numbers' in msgTitle:
        videoId = json['videoId']
        loadTrackNumbers()
        if videoId in videoTrackNumbers:
            videoTrackNumbers.pop(videoId)
            saveTrackNumbers()
        
        json['trackNumbers'] = []
        json['data'] = 'Delete Track Numbers Done'
    
    # delete a video from the playlist.
    if 'Delete Video' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
        
        # add this video to the invalid video list
        json['playListHTML'] = deleteFromPlayList(videoId, username)
            
    # delete a video from the que list for the client and if perminet add to invalid video list
    if 'Delete Qued Video' in msgTitle:
        videoId = json['videoId']
        username = json['clientId'].lower().strip()
        perminent = json['perminent']
        
        json['queListHTML'] = deleteFromQueList(videoId, username)
        json['queListData'] = getQueListData(username)

        # see if to perminately remove the video so it never show up again
        if perminent:
            print('Deleting Video:', videoId)
            addToInvalidVideoList(videoId)
    
    # check to see if a videoId is already in the playlist
    if 'Check Video Status' in msgTitle:
        videoId = json['videoId']
        
        # check to see if the video is in the merged playlist
        if videoId in userPlayList[mergedPlayListKey]:
            json['videoSaved'] = True
        else:
            json['videoSaved'] = False
        
    # get client information from the playing html client
    if 'Video Info Client Connected' in msgTitle:
        clientId = json['uname']
        mixTracks[clientId] = list() # add an entry in the mix track dictionary
        print('Video Info Client Connected:', clientId)
        
    # a new video track was loaded into the playing html client
    if 'New Video Track' in msgTitle:
        addToMixTracksDictionary(json)

    # a message asking loading progress has been received
    if 'Get Progress' in msgTitle:
        playlistUrl = json['url']
        json['loadingText'] = loadingMessages[playlistUrl]
    
    # a message to save a quelist from mp3 DJ or deckcast dj
    if 'MP3DJ Save QueList' in msgTitle:
        qname = json['qname']
        qlist = json['qlist']       
        addToSavedQueList('MP3DJ', qname, qlist)
    
    # a message to load a saved que list and return to the client
    if 'MP3DJ Load QueList' in msgTitle:
        json['qlist'] = filterSavedQueList('MP3DJ@')
    
    # a message to load a saved que list and return to the client
    if 'MP3DJ Delete QueList' in msgTitle:
        qname = json['qname'] 

        deleteFromSavedQueList('MP3DJ', qname)
        
        json['qlist'] = filterSavedQueList('MP3DJ@')
        json['data'] = 'MP3DJ Load QueList'

    # get settings
    if 'Get Settings' in msgTitle:
        json['youtubeApiKey'] = youtubeApiKey
        json['data'] = 'Settings Loaded'

    # save settings
    if 'Save Settings' in msgTitle:
        newKey = json.get('youtubeApiKey', '').strip()
        if newKey:
            saveSettings(newKey)
            pafy.set_api_key(newKey)
            json['success'] = True
            json['message'] = 'YouTube API Key saved and applied successfully!'
        else:
            json['success'] = False
            json['message'] = 'API Key cannot be empty.'
        json['data'] = 'Settings Saved'

    # reset the stored values
    if 'RESET' in msgTitle:
        userCount = 1
        player1Video = ''
        player2Video = ''
    
    return json

# route for the main desktop DJ interface
@app.route('/')
def sessions():
    return render_template('index.html', version=appVersion, bgcolor=bgColor)

# route for the mobile controller interface
@app.route('/mobile')
def mobile():
    return render_template('indexm.html', version=appVersion, bgcolor=bgColor)

# route for the MP3 playlist/player page
@app.route('/mp3')
def playMP3():
    return render_template('mp3.html', version=appVersion, bgcolor=bgColor,
                           musicBaseUrl=acquire.get_music_base_url())

# route for the CD burner page
@app.route('/burner')
def burner():
    if not burnerMayUse():
        return ('<!doctype html><title>DeckCastDJ burner</title>'
                '<h3>CD burner not available</h3><p>{0}</p>'.format(
                    burnerRefusalMessage()), 403)
    return render_template('burner.html', version=appVersion, bgcolor=bgColor)


@app.route('/burner/state')
def burnerState():
    """Plain HTTP snapshot used while polling a running job.

    Socket.IO pushes from the worker thread sit in the socket queue until the
    client sends something, so a long job could report nothing for minutes.
    A normal request/response always answers immediately.
    """
    try:
        if not burnerMayUse():
            return jsonify({'data': 'Burner Error',
                            'message': burnerRefusalMessage()}), 403
        return jsonify(burnerStatePayload(poll=True))
    except Exception as e:
        print('burner: state poll failed:', e, flush=True)
        return jsonify({'data': 'Burner Error', 'message': str(e)}), 500


@app.route('/burner/audio')
def burnerAudio():
    """Stream one premastered track so the page can preview it.

    The track is addressed by its position in the project, never by file name,
    so nothing a browser sends reaches the filesystem.
    """
    if not burnerMayUse():
        return jsonify({'data': 'Burner Error', 'message': burnerRefusalMessage()}), 403
    project = getBurnerWorking()
    try:
        index = int(request.args.get('index', '-1'))
    except (TypeError, ValueError):
        index = -1
    tracks = project.get('tracks') or []
    if not (0 <= index < len(tracks)):
        return 'No such track.', 404
    path = cdburn.premaster_path(project, tracks[index])
    if not path or not os.path.isfile(path):
        # the check above uses the path as the app knows it (relative to the
        # folder the app runs from), so the file it means goes in the message:
        # "the audio is there but the app says it is not" is otherwise a
        # guessing game
        print('burner: no audio for track {0} at {1}'.format(index, path), flush=True)
        return 'That track has not been downloaded yet ({0}).'.format(path), 404
    # conditional=True so the browser can seek with range requests.
    #
    # os.path.abspath is load-bearing: Flask resolves a *relative* path against
    # app.root_path, and in a packaged build that is the bundle folder (the
    # _internal folder next to the executable), not the folder the app runs from
    # - where the audio is and where the check above just found it.  Handing it
    # the absolute path is what makes the preview work in a distribution as well
    # as from source.
    try:
        return send_file(os.path.abspath(path), mimetype='audio/wav', conditional=True)
    except Exception as e:
        print('burner: could not send {0}: {1}'.format(path, e), flush=True)
        return 'That track could not be read ({0}).'.format(e), 404

# route for the current playing video screen (/playing)
@app.route('/playing')
def currentVideo():
    return render_template('playing.html')

# route for the VU meter display page (/vu)
@app.route('/vu')
def showVU():
    return render_template('vu.html')

# route to display a video mix page based on mixId
@app.route('/mix/<mixId>')
def getMixTracks(mixId):
    return getMixTracksHTML(mixId) 

# callback function executed when socket messages are successfully sent/received
def messageReceived(methods=['GET', 'POST']):
    print('message was received!!!')

# socket io event handler for custom client messages
@socketio.on('my event')
def handle_my_custom_event(json, methods=['GET', 'POST']):
    # CD burner messages are self contained and must never enter the DJ
    # dispatch chain, so they are intercepted before processMessage() runs.
    if str(json.get('data', '')).startswith('Burner '):
        handleBurnerMessage(json)
        return

    json = processMessage(json)
    
    # Identify client-specific private events that should not be broadcasted globally
    private_events = [
        "Edit TrackList", 
        "View Bookmarks", 
        "Get TrackList Only", 
        "Get Progress", 
        "Check Video Status",
        "View Track Numbers",
        "Extract Track Numbers Done",
        "Extract Track Numbers Error",
        "Delete Track Numbers Done",
        "Settings Loaded",
        "Settings Saved"
    ]
    
    msg_title = json.get('data', '')
    is_private = any(evt in msg_title for evt in private_events)
    
    if is_private:
        # Route exclusively to the requesting client's session ID
        socketio.emit('my response', json, room=request.sid, callback=messageReceived)
    else:
        # Broadcast globally to all connected clients
        socketio.emit('my response', json, callback=messageReceived)

if __name__ == '__main__':
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    # printed first so a second copy of the app is obvious in the terminal
    print("DeckCastDJ instance {0} listening on port {1}".format(
        BURNER_INSTANCE, app_port), flush=True)
    # the two folders everything else is found from: a packaged build runs from
    # the executable's folder, which is also where the media tree is looked for
    print("app folder   {0}".format(base_dir), flush=True)
    print("media folder {0}".format(os.path.abspath(acquire.CD_DIR)), flush=True)

    print("Loading saved playlist and que list ...\n")
    loadInvalidVideosList()
    loadPlayList()
    loadPafyCache()
    loadMixVideoTracks()
    loadSavedQueList()

    if(useYoutube):
        # Load youtube playlist for various users
        print("Loading saved youtube playlist ...\n")

        # Seed from the cached file first, so a playlist that YouTube refuses
        # to serve keeps its previous contents instead of vanishing.
        seededCount = seedUserPlayListFromCache()
        if seededCount:
            print(f"Seeded {seededCount} playlist(s) from the cached file ...\n")

        # load up some youtube playlist
        for key, playlistUrl in youtubePL.items():
            loadYouTubePlayList(key, playlistUrl)

        # a playlist that could not be refreshed is still in the list, so make
        # sure it keeps its button in the UI
        for key, playlistUrl in youtubePL.items():
            if key.lower() in userPlayList and key.title() not in youtubePlayListUrls:
                youtubePlayListUrls[key.title()] = playlistUrl
        
        print("\nDone loading youtube playlist ...\n")
    
        print("\nMerging all playlist records ...")
    
        mergeAllPlayList()
        saveUserPlayList()
        
        print("Merging done ...")
        
        if stalePlayListKeys:
            print("Served from cache (YouTube refresh unavailable): "
                  + ", ".join(sorted(stalePlayListKeys)))
    else:
        print("\nLoading backup playlist ...\n")
        loadBackupUserPlayList()
        print("Done loading backup ...\n")
    
    # load the video track list
    loadTrackLists()
    
    # load the bookmarks database
    loadBookmarks()
    
    # load the track numbers database
    loadTrackNumbers()

    # load saved CD burner projects
    cdburn.load_projects()
    
    serverStarted = True
    
    socketio.run(app, host = '0.0.0.0', debug = False, port = app_port, allow_unsafe_werkzeug=True)