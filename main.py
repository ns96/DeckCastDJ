# -*- coding: utf-8 -*-
"""
Created on Thu May  7 08:35:27 2020

A simple flask/SocketIO for building very simple youtube DJ application that
can be shared by other users

@author: Nathan
@version: 1.7.1 (07/29/2024)
"""
import os.path
from datetime import datetime, timedelta
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import pafy
import qrcode
from config import youtubeApiKey

# set the youtube api key. 
pafy.set_api_key(youtubeApiKey)

from flask import Flask, render_template, request
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret12345#'
socketio = SocketIO(app)

defaultPlayListFile = 'data/playlist.json'
userPlayListFile = 'data/userPlaylist.json'
invalidVideosFile = 'data/invalidVideos.json'

defaultPlayList = dict() # the default playlist for "guest"
userPlayList = dict()
youtubePlayListUrls = dict()
invalidVideosList = list()
pafyCache = dict()

# dictionary to store the tracklist for a video
trackListsFile = 'data/tracklists.json'
videoTrackLists = dict()

# key for playlist that holds all the videoIds stored
mergedPlayListKey = ''

# keep track of the number of connected users
userCount = 0
player1Video = ''
player2Video = ''

# the admin pin used for testing and other things
adminPin = "0001"

# function to upgrade the guest playlist. Only called when additional information
# needs to be added to playList records
def upgradeDefaultPlayListInfo():
    global defaultPlayList
    
    loadPlayList()
    
    # keep track of bad videos
    unv_videos = list()
    
    for videoId in defaultPlayList.keys():
        try:
            videoInfo = getVideoInfo(videoId, "Guest")
            defaultPlayList[videoId] = videoInfo
            print("Updated: " + videoId + " " + str(videoInfo))
        except Exception as exp:
            unv_videos.append(videoId)
            print("\n\nUnable to Upgrade: ", videoId, "\n")
            print(exp, "\n\n")
            
    # clean up the playlist so we don't have records for videos that no longer exists
    for videoId in unv_videos:
        defaultPlayList.pop(videoId)
    
    # save the new playlist
    with open(defaultPlayListFile, 'w') as fp:
        json.dump(defaultPlayList, fp, indent=2)

# function to upgrade a users like playlist
def upgradeUserPlayListInfo(username):
    username = username.lower()
    
    filename = 'likes_' + username + '.json'
    if os.path.isfile(filename):
        print("Upgrading", username, "playlist information ...\n")
        
        with open(filename) as json_file: 
            playlist = json.load(json_file)
        
        # keep track of bad videos
        unv_videos = list()
    
        for videoId in playlist.keys():
            try:
                videoInfo = getVideoInfo(videoId, username)
                playlist[videoId] = videoInfo
                print("Updated: " + videoId + " " + str(videoInfo))
            except Exception as exp:
                unv_videos.append(videoId)
                print("\n\nUnable to Upgrade: ", videoId, "\n")
                print(exp, "\n\n")
            
        # clean up the playlist so we dont have records for videos that no longer exists
        for videoId in unv_videos:
            playlist.pop(videoId)
    
        # save the new playlist
        with open(filename, 'w') as fp:
            json.dump(playlist, fp, indent=2)
        
# save the playlist as json
def savePlayList():    
    with open(defaultPlayListFile, 'w') as fp:
        json.dump(defaultPlayList, fp, indent=2)

# save the youtube playlist as json
def saveUserPlayList():    
    with open(userPlayListFile, 'w') as fp:
        json.dump(userPlayList, fp, indent=2)

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
    
    '''
    for i in range(2):
        videoId = 'videoId_' + str(i)
        title = 'Video Title 2020 # ' + str(i)
        thumbnail = 'https://www.gyanblog.com/assets/img/2018/youtube-logo.jpg'
        playList[videoId] = [title, thumbnail]
    '''

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
    
        for video in youtubeList:
            playlist[video.videoid] = [video.title, video.thumb, video.duration, video.published, username]
            print(video.videoid, video.title, video.thumb, video.duration, "\n")
    
        userPlayList[username] = playlist
        
        if not forQue:
            youtubePlayListUrls[playlistKey] = url
        
        #checkPlayList(playlist)
    
        print("YouTube playlist loaded: " + youtubeList.title + " / " + str(len(playlist)))
    except Exception as e:
        print("Error loading YouTube playlist ...\n", url, "\n" , e)

# save the playlist as json
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
def copyPlayListToQue(username, playlistKey):
    global userPlayList

    duplicatePlaylist = dict()
    
    if playlistKey in userPlayList.keys():
        playlist = userPlayList.get(playlistKey)
    else:
        playlist = defaultPlayList

    for videoId in playlist.keys():
        duplicatePlaylist[videoId] = playlist[videoId]

    userPlayList[username] = duplicatePlaylist
    print("Playlist duplicated", playlistKey, '->', username)

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

# function to check if the playlist has any videos which were deleted
# from youtube
def checkPlayList(videoList):
    global invalidVideos
    
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

# check to see if video exist. Doesn't always work
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
def getHTMLTable(username = "", filter_text = "", que_list = False, sort = True):
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
        tableHtml += '<b>YouTube Playlist (<a href="#filter_list"> Filter </a>): </b>'
        tableHtml += '<input type="button" onclick="loadPlayListForUser(\'Guest\', \' \', true)" value="Guest"> '
        
        for playlistName in youtubePlayListUrls.keys():
            tableHtml += '<input type="button" onclick="loadPlayListForUser(\'' + playlistName + '\', \' \', true)" value="' + playlistName + '"> '
        
        # add button to allow moving playlist to que
        tableHtml += '<input type="button" onclick="queSavedPlayList()" value="Q"> '

        # add button to clear playlist
        tableHtml += '<input type="button" onclick="clearPlayList()" value="Clear"> '
    else:
        #sortedList = cleanPlayList(sortedList)
        queListString, totalTime, queListTimesString = getQueListString(sortedList)
        
        qsize = len(sortedList)
        tableHtml += '<b>Qued Video(s): ' + str(qsize) + '</b> '
        tableHtml += '<input type="button" onclick="clearQueList()" value="Clear Que"> '
        tableHtml += '<input type="button" onclick="playQueList(\'' + queListString + '\')" value="Play All ( ' + totalTime + ' )"> '
        tableHtml += '<input type="button" onclick="mixQueList(\'' + queListString + '\',\'' + queListTimesString + '\')" value="Play Mix"> '
        tableHtml += 'Start @ Video: <input type="text" id="startMixAt" name="startMixAt" value="1" size="1"> '
        tableHtml += 'Overlap: <input type="text" id="mixOverlap" name="mixOverlap" value="20" size="2"> Seconds || '
        tableHtml += 'Play Percent: <input type="text" id="mixPlayPercent" name="mixPlayPercent" value="60" size="2"> '
        tableHtml += '<input type="button" onclick="stopMixPlay()" value="Stop Mix"><br>'
        tableHtml += '<span id="queMixMessage">Curent Mix Track: 0 / Playtime: 0000 seconds</span>'
    
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
                tableHtml += getHTMLTableRow(i, que_list, videoId, title, meta_info, videoInfo)        
                i += 1
        elif filter_text == "" or filter_text in title_lower:
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
    rowHtml += '<td><b>' + str(i) + '.</b></td>'
    rowHtml += '<td width="25%"><b>' + title + '</b><br>[' + meta_info + '] (<a href="#pageTop"> Top </a>)</td>'
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
        # might make mosre sense to just return son array
        queData.append(videoId + '\t' + videoTitle + '\t' + str(videoSeconds))

    return queData
   
# get the title and thumbnail image for the video
# https://stackoverflow.com/questions/59627108/retrieve-youtube-video-title-using-api-python
def getVideoInfo(videoId, username):
    '''
    params = {"format": "json", "url": "https://www.youtube.com/watch?v=%s" % videoId}
    url = "https://www.youtube.com/oembed"
    query_string = urllib.parse.urlencode(params)
    url = url + "?" + query_string

    with urllib.request.urlopen(url) as response:
        response_text = response.read()
        data = json.loads(response_text.decode())
        
    return ([data['title'], data['thumbnail_url'], "--:--:--", username])
    '''
        
    url = "http://www.youtube.com/watch?v=" + videoId
    video = pafy.new(url)            
    return [video.title, video.thumb, video.duration, video.published, username]
    
def addToPlayList(videoId, username):
    global defaultPlayList
    
    defaultPlayList[videoId] = getVideoInfo(videoId, username)
    savePlayList()
    
    return getHTMLTable()

def deleteFromPlayList(videoId, username):
    global defaultPlayList, userPlayList, invalidVideosList
    
    # see if it's a user playlist or the default guest list
    videolist = userPlayList.get(username);
    if videolist == None:
        videolist = defaultPlayList
        
    if videoId in videolist:
        videolist.pop(videoId)
        invalidVideosList.append(videoId)
    
        # save the invalid video list now
        with open(invalidVideosFile, 'w', encoding='utf-8') as f:
            json.dump(invalidVideosList, f, ensure_ascii=False, indent=4)
    
    return getHTMLTable(username)

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

def deleteFromQueList(videoId, username):
    global userPlayList
    
    playlist = userPlayList.get(username);
        
    if videoId in playlist:
        playlist.pop(videoId)
    
    return getHTMLTable(username, "", True, False)

def clearQueList(username):
    global userPlayList
    
    if username in userPlayList:
        userPlayList.pop(username)
    
    return ""

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

# get the current information for the video being played
def getCurrentVideoInfoHTML(videoId, videoInfo):
    qrImg = createQRCode(videoId)
    soundBarImg = 'https://i.pinimg.com/originals/31/12/81/31128181420688cf4eda6579ef7dfcc9.gif'
    
    
    tableHtml = '<font size="+7">' + videoInfo[0] + ' | ' + videoInfo[2] + '</font><br>'
    tableHtml += '<font size="+3"><span align="center" id="tracklist"></span></font>'
    tableHtml += '<table cellpadding="2" cellspacing="0" border="0" width="100%">'
    tableHtml += '<tr>'
    tableHtml += '<td><font size="+5"><span id="track">Track # 1</span></font></td>'
    tableHtml += '<td><font size="+5"><span id="timer"></span></font></td>'
    tableHtml += '</tr>'
    tableHtml += '<tr>'
    tableHtml += '<td style="text-align: center;"><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="360" height="270"><br>' 
    tableHtml += '<img src="' + soundBarImg + '" alt="Video Thumbnail" width="360" height="270">'
    tableHtml += '</td>'
    tableHtml += '<td bgcolor="white" style="text-align: center;"><img src="' + qrImg + '" alt="QRCode"></td>'
    tableHtml += '</tr></table>'
    
    return tableHtml

# get the current information for the video being played
def getLiteCurrentVideoInfoHTML(videoId, videoInfo):
    qrImg = createQRCode(videoId)
    
    tableHtml = '<font size="+5">' + videoInfo[0] + ' | ' + videoInfo[2] + '</font><br>'
    tableHtml += '<table cellpadding="2" cellspacing="0" border="0" width="100%">'
    tableHtml += '<tr>'
    tableHtml += '<td style="text-align: center;"><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="360" height="270"></td>' 
    tableHtml += '<td bgcolor="white" style="text-align: center;"><img src="' + qrImg + '" alt="QRCode" width="215" height="215"></td>'
    tableHtml += '</tr></table>'
    
    return tableHtml

def processMessage(json):
    global defaultPlayList, userCount, player1Video, player2Video
    
    print("Processing msg: " + str(json));
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
        
        # check to see if to to load a youtube playlist
        if 'https://www.youtube.com/playlist' in f_text:
            playListUser = getUniquePlayListName(playListUser)
            loadYouTubePlayList(playListUser, f_text)
            mergeAllPlayList() # create a new merge playlist
            f_text = ""
            
        json['playListHTML'] = getHTMLTable(username = playListUser, filter_text = f_text.lower())
    
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
        
        # check to see if to to load a youtube playlist and add to que
        if 'https://www.youtube.com/playlist' in videoId:
            loadYouTubePlayList(username, videoId, True)
            json['queListHTML'] = getHTMLTable(username, "", True, False)
            json['queListData'] = getQueListData(username)
        elif 'savedList:' in videoId:
            savedList = videoId.split(":")[1]
            copyPlayListToQue(username.lower(), savedList.lower())
            json['queListHTML'] = getHTMLTable(username, "", True, False)
            json['queListData'] = getQueListData(username)
        elif videoId != "0":
            json['queListHTML'] = addToQueList(videoId, username)
            json['queListData'] = getQueListData(username)
        else:
            json['queListHTML'] = clearQueList(username)
    
    # see if to save the video to the playlist
    if 'Video Saved' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
                
        # only save videos for the secret guest0 to prevent anyone from making a 
        # mess of the playlist. A better way would be to use seperate pin variable
        if username == "guest0":
            if videoId not in defaultPlayList:
                json['playListHTML'] = addToPlayList(json['videoId'], "guest")
                json['savedVideo'] = "Video Saved To Playlist ..."
            else:
                json['savedVideo'] = "Video Already in Playlist ..."
    
    # see if to broadcast the video that currently playing
    if 'Current Video' in msgTitle:
        videoId = json['videoId']
        pin = json['pin']
        # clientId = json['clientId'].lower().strip()
                
        # return information on current video
        videoInfo = getVideoInfo(videoId, "N/A")
        pt = datetime.strptime(videoInfo[2], '%H:%M:%S')
        totalSeconds = pt.second + pt.minute*60 + pt.hour*3600
        
        json['videoTitle'] = videoInfo[0] + ' | ' + videoInfo[2]
        json['videoInfoHTML'] = getCurrentVideoInfoHTML(videoId, videoInfo)
        json['videoInfoLiteHTML'] = getLiteCurrentVideoInfoHTML(videoId, videoInfo)
        json['videoTime'] = totalSeconds

        if videoId in videoTrackLists:
            json['trackList'] = videoTrackLists[videoId]

    # see if to add the tracklist for a particular video
    if 'Add TrackList' in msgTitle:
        videoId = json['videoId']
        pin = json['pin']
        tracklist = json['tracklist'].strip()
                
        # only add tracklist for admin pin
        if pin == adminPin:
            if len(tracklist) != 0:
                addTrackListForVideo(videoId, tracklist)
    
    # delete a video from the playlist.
    if 'Delete Video' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
        
        # add this video to the ivalid video list
        json['playListHTML'] = deleteFromPlayList(videoId, username)
            
    # delete a video from the que list for the client
    if 'Delete Qued Video' in msgTitle:
        videoId = json['videoId']
        username = json['clientId'].lower().strip()
        
        json['queListHTML'] = deleteFromQueList(videoId, username)
        json['queListData'] = getQueListData(username)
        
    # reset the stored values
    if 'RESET' in msgTitle:
        userCount = 1
        player1Video = ''
        player2Video = ''
    
    return json

@app.route('/')
def sessions():
    return render_template('index.html')

@app.route('/playing')
def current():
    return render_template('playing.html')

def messageReceived(methods=['GET', 'POST']):
    print('message was received!!!')

@socketio.on('my event')
def handle_my_custom_event(json, methods=['GET', 'POST']):
    json = processMessage(json)
    socketio.emit('my response', json, callback=messageReceived)

if __name__ == '__main__':
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    
    #upgradeGuestPlayListInfo()
    #upgradeUserPlayListInfo('Nathan')
    
    # used for loading playlist from youtube. 
    # If false, a backup is loaded from disk
    useYouTube = False 
                      
    print("Loading local playlist ...\n")
    loadInvalidVideosList()
    loadPlayList()

    # No longer loading likes videos from json file, just load from youtube 
    # playlist. Make it easier to update
    #loadUserPlayList('Nathan')
    
    if(useYouTube):
        # Load youtube playlist for various users
        print("Loading youtube playlist ...\n")
        
        # change these url to your own youtube playlist
        loadYouTubePlayList('Denvers Favorite', 'https://www.youtube.com/playlist?list=PLgASkX6vGzmBGM1RYaiS2Ga2vcz-C1AZQ')
        loadYouTubePlayList('80s Hits', 'https://www.youtube.com/playlist?list=PLmXxqSJJq-yXrCPGIT2gn8b34JjOrl4Xf')
        loadYouTubePlayList('Nathan Likes', 'https://www.youtube.com/playlist?list=PL9nM-OJA81WAhh0jaDiT228tPzJeSS-OP')

        print("\nDone loading youtube playlist ...\n")
    
        print("\nMerging all playlist records ...")
    
        mergeAllPlayList()
        saveUserPlayList()
        
        print("Merging done ...")
    else:
        print("Loading backup playlist ...\n")
        loadBackupUserPlayList();
        print("Done loading backup ...\n")
    
    # load the video track list
    loadTrackLists()
    
    socketio.run(app, host = '0.0.0.0', debug = False, port = 5054, allow_unsafe_werkzeug=True)