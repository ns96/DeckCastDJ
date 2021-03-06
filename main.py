# -*- coding: utf-8 -*-
"""
Created on Thu May  7 08:35:27 2020

A simple flask/SocketIO for building very simple youtube DJ application that
can be shared by other users

@author: Nathan
@version: 0.9.0 (03/06/2021)
"""
import os.path
from urllib.request import urlopen
from urllib.error import HTTPError
#import urllib
import json
import pafy

# set the youtube api key. change this before pushing code to public repo!!!
pafy.set_api_key('YourAPIKeyGoesHere')

from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret12345#'
socketio = SocketIO(app)

playListFile = 'playlist.json'
playList = dict()
userPlayList = dict()
youtubePlayList = dict();
invalidVideos = list()

# keep track of the number of connected users
userCount = 0
player1Video = ''
player2Video = ''

# function to upgrade the guest playlist. Only called when additional information
# needs to be added to playList records
def upgradeGuestPlayListInfo():
    global playList
    
    loadPlayList()
    
    # keep track of bad videos
    unv_videos = list()
    
    for videoId in playList.keys():
        try:
            videoInfo = getVideoInfo(videoId, "Guest")
            playList[videoId] = videoInfo
            print("Updated: " + videoId + " " + str(videoInfo))
        except Exception as exp:
            unv_videos.append(videoId)
            print("\n\nUnable to Upgrade: ", videoId, "\n")
            print(exp, "\n\n")
            
    # clean up the playlist so we dont have records for videos that no longer
    # exists
    for videoId in unv_videos:
        playList.pop(videoId)
    
    # save the new playlist
    with open(playListFile, 'w') as fp:
        json.dump(playList, fp, indent=2)

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
            
        # clean up the playlist so we dont have records for videos that no longer
        # exists
        for videoId in unv_videos:
            playlist.pop(videoId)
    
        # save the new playlist
        with open(filename, 'w') as fp:
            json.dump(playlist, fp, indent=2)
        
# save the playlist as json
def savePlayList():
    global playList
    
    with open(playListFile, 'w') as fp:
        json.dump(playList, fp, indent=2)

# function to load the default playlist from file
def loadPlayList():
    global playList
    
    if os.path.isfile(playListFile):
        with open(playListFile) as json_file: 
            playList = json.load(json_file)
            #checkPlayList(playList)
    
    '''
    for i in range(2):
        videoId = 'videoId_' + str(i)
        title = 'Video Title 2020 # ' + str(i)
        thumbnail = 'https://www.gyanblog.com/assets/img/2018/youtube-logo.jpg'
        playList[videoId] = [title, thumbnail]
    '''

# function to load a playlist 
def loadYouTubePlayList(username, url):
    global userPlayList, youtubePlayList
    
    try:
        playlistKey = username.title()
        username = username.lower()
        
        youtubeList = pafy.get_playlist2(url)
        playlist = dict()
    
        for video in youtubeList:
            playlist[video.videoid] = [video.title, video.thumb, video.duration, video.published, username]
            #print(video.videoid, video.title, video.thumb, video.duration, "\n")
    
        userPlayList[username] = playlist
        youtubePlayList[playlistKey] = url
        
        #checkPlayList(playlist)
    
        print("YouTube playlist loaded: " + youtubeList.title + " / " + str(len(playlist)))
    except Exception as e:
        print("Error loading YouTube playlist ...\n", url, "\n" , e)

# function to load a users playlist from a file
def loadUserPlayList(username):
    global userPlayList
    username = username.lower()
    
    filename = 'likes_' + username + '.json'
    if os.path.isfile(filename):
        with open(filename) as json_file: 
            playlist = json.load(json_file)
            userPlayList[username] = playlist
            #checkPlayList(playlist)
    
    # add it to the youtube playlist
    playlistKey = username.title()
    youtubePlayList[playlistKey] = "dummy url"
    
    print("User playlist loaded: " + str(len(userPlayList)))

# function to check if the playlist has any videos which were deleted
# from youtube
def checkPlayList(videoList):
    global invalidList
    
    print("Skipping video check ...")
    
    for videoId in videoList:
        videoInfo = videoList[videoId]
                
        try:
            print("Video Exist:\t", videoId, urlopen(videoInfo[1]).getcode())        
        except HTTPError as err:
            print("Video Deleted:\t", videoId, err.code)
            invalidVideos.append(videoId)
             
# function get sorted list
def sortPlayList(playlist):    
    sorted_d = sorted(playlist.items(), key=lambda x: x[1])    
    return sorted_d

# clean up the publish timestamp. For now just return the year-month-day part
def cleanDate(publishTS):
    return publishTS.split()[0]
    
# function to create an html table consisting of the playList
def getHTMLTable(username = "", filter_text = "", que_list = False):
    global playList, userPlayList, invalidVideos, youtubePlayList
    
    sortedList = sortPlayList(playList)
    
    if (username != "") and (username in userPlayList):
        userlist = userPlayList[username]
        sortedList = sortPlayList(userlist)
    
    print('Generating playlist html...')
    
    # add the buttons for the playlist
    tableHtml = ""
    
    if not que_list:
        tableHtml += '<b>YouTube Playlist: </b>'
        for playlistName in youtubePlayList.keys():
            tableHtml += '<input type="button" onclick="loadPlayListForUser(\'' + playlistName + '\', \' \', true)" value="' + playlistName + '"> '
    else:
        tableHtml += '<b>Que List: </b>'
        
    tableHtml += '<br><br><table cellpadding="2" cellspacing="0" border="0" width="100%">'
    
    i = 1
    for video in sortedList:
        videoId = video[0]
        videoInfo = video[1]
        
        # check to see if this video is in the invalid list
        if videoId in invalidVideos:
            print("Invalid Video -- Deleted:\t", videoId)
            continue
        
        published = cleanDate(videoInfo[3])
        
        title = videoInfo[0] + " (-" + published.split('-')[0] + "-)"
        title_lower = title.lower()
        
        meta_info = videoId + " / " + published
        
        if filter_text == "" or filter_text in title_lower:
            # if odd, add new row tag
            if i % 2 == 0:
                rowHtml = ' '
            else:
                rowHtml = '<tr>'
            
            rowHtml += '<td>'
            rowHtml += '<input type="button" onclick="loadVideoForPlayer(1,\'' + videoId + '\')" value="<"> <br>'
            rowHtml += '<input type="button" onclick="loadVideoForPlayer(2,\'' + videoId + '\')" value=">"> <br>'
            rowHtml += '<input type="button" onclick="removeVideoFromList(\'' + videoId + '\')" value="X">'
            rowHtml += '</td>'
            rowHtml += '<td><b>' + str(i) + '.</b></td>'
            rowHtml += '<td width="25%"><b>' + title + '</b><br>[' + meta_info + ']</td>'
            rowHtml += '<td><b>' + videoInfo[2] + '</b></td>'
            rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="120" height="90"></td>'
            #rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail"></td>'
            rowHtml += '<td>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>'
            
            # if even, add close row tag
            if i % 2 == 0:
                rowHtml += '</tr>'
            
            tableHtml += rowHtml
        
            i += 1
    
    # check to see if to add the end row tag
    if not tableHtml.endswith('</tr>'):
        tableHtml += '</tr>'
        
    tableHtml += '</table>'
    
    #print(tableHtml)
    return(tableHtml)
    
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
    
    # 10/30/2020 -- Pafy library is now broken becuause yt-dl no longer works
    # will eventual need to go to using the youtube api since duration is not 
    # available from above code
    # 11/6/2020 -- pafy is now working again
    
    url = "http://www.youtube.com/watch?v=" + videoId
    video = pafy.new(url)            
    return([video.title, video.thumb, video.duration, video.published, username])
    
def addToPlayList(videoId, username):
    global playList
    
    playList[videoId] = getVideoInfo(videoId, username)
    savePlayList()
    
    return getHTMLTable()

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
    
    return getHTMLTable(username, "", True)

def processMessage(json):
    global playList, userCount, player1Video, player2Video
    
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
    
    # if we load a playlist indicate as much
    if 'Load PlayList' in msgTitle:
        username = json['uname'].lower().strip()
        f_text = json['filter'].strip()
        
        # check to see if to to load a youtube playlist
        if 'https://www.youtube.com/playlist' in f_text:
            loadYouTubePlayList(username, f_text)
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
        
        if (videoId not in playList) and (username == "" or username == "guest"):
            json['playListHTML'] = addToPlayList(json['videoId'], username)
    
    # see if to add the video to the que list for the client
    if 'Video Qued' in msgTitle:
        videoId = json['videoId']
        username = json['clientId'].lower().strip()
        json['queListHTML'] = addToQueList(json['videoId'], username)    
    
    # delete a video from the playlist.
    if 'Delete Video' in msgTitle:
        videoId = json['videoId']
        username = json['uname'].lower().strip()
        # TO-DO Delete video from playlist and reload it
        print("Deleted video:", videoId, "from playlist:", username)
    
    # reset the stored values
    if 'RESET' in msgTitle:
        userCount = 1
        player1Video = ''
        player2Video = ''
    
    return json

@app.route('/')
def sessions():
    return render_template('index.html')

def messageReceived(methods=['GET', 'POST']):
    print('message was received!!!')

@socketio.on('my event')
def handle_my_custom_event(json, methods=['GET', 'POST']):
    json = processMessage(json)
    socketio.emit('my response', json, callback=messageReceived)

if __name__ == '__main__':
    #upgradeGuestPlayListInfo()
    #upgradeUserPlayListInfo('Nathan')
    
    print("Loading user/youtube playlist ...\n\n")
    loadPlayList()
    loadUserPlayList('Nathan')
    
    # Load youtube playlist for various users
    loadYouTubePlayList('Denvers Favorite', 'https://www.youtube.com/playlist?list=PLgASkX6vGzmBGM1RYaiS2Ga2vcz-C1AZQ')
    loadYouTubePlayList('trinidad soca 2020', 'https://www.youtube.com/playlist?list=PLtytcEbClKCyqQ6wl_0H4a1ZuzjaFGlVi')
    #loadYouTubePlayList('Soca 400+ Videos', 'https://www.youtube.com/playlist?list=PLFD51ECAD4E496954')
    
    print("\nDone loading user playlist ...")
    
    socketio.run(app, host = '0.0.0.0', debug = False, port = 5054)