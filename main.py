# -*- coding: utf-8 -*-
"""
Created on Thu May  7 08:35:27 2020

A simple flask/SocketIO for building very simple youtube DJ application that
can be shared by other users

@author: Nathan
@version: 0.5.4 (10/07/2020)
"""
import os.path
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import pafy

from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret12345#'
socketio = SocketIO(app)

playListFile = 'playlist.json'
playList = dict()
userPlayList = dict()
invalidVideos = list()

# keep track of the number of connected users
userCount = 0
player1Video = ''
player2Video = ''

# function to upgrade the playlist
def upgradePlayListInfo(username):
    global playList
    
    loadPlayList()
        
    for videoId in playList.keys():
        videoInfo = playList[videoId]
        videoInfo.append(username)
        playList[videoId] = videoInfo
        print("Updated: " + videoId + " " + str(videoInfo))
    
    # save the new playlist
    with open(playListFile, 'w') as fp:
        json.dump(playList, fp, indent=2)
        
# save the playlist as json
def savePlayList():
    global playList
    
    with open(playListFile, 'w') as fp:
        json.dump(playList, fp, indent=2)

# function to load the playlist from file
def loadPlayList():
    global playList, loadedPL
    
    if os.path.isfile(playListFile):
        with open(playListFile) as json_file: 
            playList = json.load(json_file)
            checkPlayList(playList)
    
    '''
    for i in range(2):
        videoId = 'videoId_' + str(i)
        title = 'Video Title 2020 # ' + str(i)
        thumbnail = 'https://www.gyanblog.com/assets/img/2018/youtube-logo.jpg'
        playList[videoId] = [title, thumbnail]
    '''

# function to load the playlist from a file
def loadUserPlayList(username):
    global userPlayList
    username = username.lower()
    
    filename = 'likes_' + username + '.json'
    if os.path.isfile(filename):
        with open(filename) as json_file: 
            playlist = json.load(json_file)
            userPlayList[username] = playlist
            checkPlayList(playlist)
    
    print("User playlist loaded: " + str(len(userPlayList)))

# function to check if the playlist has any videos which were deleted
def checkPlayList(videoList):
    global invalidList, videoInfo
    
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

# function to create an html table consisting of the playList
def getHTMLTable(username = "", filter_text = ""):
    global playList, userPlayList, invalidVideos
    
    sortedList = sortPlayList(playList)
    
    if (username != "") and (username in userPlayList):
        playlist = userPlayList[username]
        sortedList = sortPlayList(playlist)
    
    print('Generating playlist html...')
    
    tableHtml = '<table style="width:100%">'
    
    i = 1
    for video in sortedList:
        videoId = video[0]
        videoInfo = video[1]
        
        # check to see if this video is in the invalid list
        if videoId in invalidVideos:
            print("Invalid Video -- Deleted:\t", videoId)
            continue
        
        title = videoInfo[0]
        title_lower = title.lower()
        
        if filter_text == "" or filter_text in title_lower:
            # if odd add new row tag
            if i % 2 == 0:
                rowHtml = ' '
            else:
                rowHtml = '<tr>'
            
            rowHtml += '<td>'
            rowHtml += '<input type="button" onclick="loadVideoForPlayer(1,\'' + videoId + '\')" value="<"> '
            rowHtml += '<input type="button" onclick="loadVideoForPlayer(2,\'' + videoId + '\')" value=">"> '
            rowHtml += '<input type="button" onclick="removeVideoFromList(\'' + videoId + '\')" value="X">'
            rowHtml += '</td>'
            rowHtml += '<td><b>' + str(i) + '.</b></td>'
            rowHtml += '<td><b>' + title + '</b></td>'
            rowHtml += '<td><b>' + videoInfo[2] + '</b></td>'
            #rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail" width="153" height="86"></td>'
            rowHtml += '<td><img src="' + videoInfo[1] + '" alt="Video Thumbnail"></td>'
            
            # if even close row
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
    
# get the title and thumnail image for the video
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
        return (data['title'], data['thumbnail_url'])
    '''
    url = "http://www.youtube.com/watch?v=" + videoId
    video = pafy.new(url)
    return([video.title, video.thumb, video.duration, username])
    
def addToPlayList(videoId, username):
    global playList
    
    playList[videoId] = getVideoInfo(videoId, username)
    savePlayList()
    
    return getHTMLTable()

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
        f_text = json['filter'].lower().strip()
        json['playListHTML'] = getHTMLTable(username = username, filter_text = f_text)
    
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
    #upgradePlayListInfo('Guest')
    loadPlayList()
    loadUserPlayList('Nathan')
    #sortPlayList()
    #print(getHTMLTable())
    socketio.run(app, host = '0.0.0.0', debug = False, port = 5054)