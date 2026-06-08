# -*- coding: utf-8 -*-
"""
Created on Thu May  7 08:35:27 2020

A simple flask/SocketIO for building very simple youtube DJ application that
can be shared by other users

@author: Nathan
@version: 1.18.0 (06/08/2026)
"""
# this variables are passed onto the html templates
appVersion = 'v1.18.0 (06/08/2026)'
bgColor = '#b2b2de' # no longer used but will keep for backward compatibility

import os.path
from datetime import datetime, timedelta
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import pafy
import qrcode
import pickle
from config import app_port, youtubeApiKey, useYoutube, youtubePL
from flask import Flask, render_template, request
from flask_socketio import SocketIO

os.chdir(os.path.dirname(__file__))

# set the youtube api key. 
pafy.set_api_key(youtubeApiKey)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret12345#'
socketio = SocketIO(app)

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

# dictionary to hold tracks being displayed on /playing html page
mixTracks = dict()
mixTracksFile = 'data/mixTrack.json' 

# keys for playlist that holds all the videoIds
mergedPlayListKey = ''

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

# function to load a youtube playlist 
def loadYouTubePlayList(username, url, forQue=False):
    global userPlayList, youtubePlayListUrls
    
    try:
        playlistKey = username.title()
        username = username.lower()
        
        if url in pafyCache.keys():
            youtubeList = pafyCache[url]
        else:
            youtubeList = pafy.get_playlist2(url)
            pafyCache[url] = youtubeList

        # store information about the videos in playlist
        playlist = dict()
        playlistSize = youtubeList._len
        videoCount = 0

        for video in youtubeList:
            if video.videoid not in invalidVideosList:
                videoCount += 1
                loadingText = f'Loading {videoCount} / {playlistSize} ...'
                loadingMessages[url] = loadingText

                playlist[video.videoid] = [video.title, video.thumb, video.duration, video.published, username]
                print(loadingText, video.videoid, video.title, video.thumb, video.duration, "\n")
                
                # Yield control cooperatively to let the server handle progress polls (only when active)
                if serverStarted:
                    socketio.sleep(0.01)
            else:
                print("Deleted video:", video.videoid)
    
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
def loadQueuePlaylistInBackground(username, url, clientId):
    try:
        loadYouTubePlayList(username, url, True)
        
        # Emitting success payload back to this specific client
        html = getHTMLTable(username, "", True, False)
        data_list = getQueListData(username)
        socketio.emit('my response', {
            'data': 'Video Qued Done',
            'queListHTML': html,
            'queListData': data_list,
            'clientId': clientId
        })
    except Exception as e:
        print("Error in loadQueuePlaylistInBackground:", e)
        socketio.emit('my response', {
            'data': 'Error Loading YouTube Playlist ...',
            'queListHTML': '<div>Error Loading Queue Playlist</div>',
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
        tableHtml += '<input type="button" onclick="stopMixPlay()" value="Stop Mix"> '
        tableHtml += '<input type="button" onclick="checkQueList()" value="Check"><br>'
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

    return queData
   
# get the title and thumbnail image for the video
# https://stackoverflow.com/questions/59627108/retrieve-youtube-video-title-using-api-python
def getVideoInfo(videoId, username):        
    url = "http://www.youtube.com/watch?v=" + videoId
    video = pafy.new(url)            
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
        if 'https://www.youtube.com/playlist' in f_text:
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
        if (username == "guest0") and ('https://www.youtube.com/playlist' in f_text):
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
        
        # check to see if to to load a youtube playlist and add to que
        if 'https://www.youtube.com/playlist' in videoId:
            socketio.start_background_task(
                loadQueuePlaylistInBackground, username, videoId, clientId
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
        else:
            json['trackList'] = []

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
            # append as sublist: [time, description]
            videoBookmarks[videoId].append([round(time, 2), f"Bookmark by {uname}"])
            # sort chronologically by time
            videoBookmarks[videoId].sort(key=lambda x: x[0])
            saveBookmarks()
            
        json['bookmarks'] = videoBookmarks[videoId]
        # Redirect data to View Bookmarks so it opens the dialog
        json['data'] = 'View Bookmarks'

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
    return render_template('mp3.html', version=appVersion, bgcolor=bgColor)

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
    json = processMessage(json)
    
    # Identify client-specific private events that should not be broadcasted globally
    private_events = [
        "Edit TrackList", 
        "View Bookmarks", 
        "Get TrackList Only", 
        "Get Progress", 
        "Check Video Status"
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
                       
    print("Loading saved playlist and que list ...\n")
    loadInvalidVideosList()
    loadPlayList()
    loadPafyCache()
    loadMixVideoTracks()
    loadSavedQueList()

    if(useYoutube):
        # Load youtube playlist for various users
        print("Loading saved youtube playlist ...\n")
        
        # load up some youtube playlist
        for key, playlistUrl in youtubePL.items():
            loadYouTubePlayList(key, playlistUrl)
        
        print("\nDone loading youtube playlist ...\n")
    
        print("\nMerging all playlist records ...")
    
        mergeAllPlayList()
        saveUserPlayList()
        
        print("Merging done ...")
    else:
        print("\nLoading backup playlist ...\n")
        loadBackupUserPlayList()
        print("Done loading backup ...\n")
    
    # load the video track list
    loadTrackLists()
    
    # load the bookmarks database
    loadBookmarks()
    
    serverStarted = True
    
    socketio.run(app, host = '0.0.0.0', debug = False, port = app_port, allow_unsafe_werkzeug=True)