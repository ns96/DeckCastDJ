# -*- coding: utf-8 -*-
"""
Created on Sat May  9 12:56:30 2020

A simple script to process a users youtube likes and stream line for use with the main script

https://stackoverflow.com/questions/59627108/retrieve-youtube-video-title-using-api-python
@author: Nathan
"""
import urllib.request
import json
import urllib
import pafy

#change to your VideoID or change url inparams
VideoID = "iWe2R0gtHX0" 

userLikes = dict()

pafy.set_api_key('YourAPIKeyGoesHere')

def getYouTubeInfo(VideoID, username='Guest'):
    global data
    
    params = {"format": "json", "url": "https://www.youtube.com/watch?v=%s" % VideoID}
    url = "https://www.youtube.com/oembed"
    query_string = urllib.parse.urlencode(params)
    url = url + "?" + query_string

    with urllib.request.urlopen(url) as response:
        response_text = response.read()
        data = json.loads(response_text.decode())
        print([data['title'], data['thumbnail_url'], "---", username])
    
def getYouTubeInfo2(VideoID, username):
    url = "http://www.youtube.com/watch?v=" + VideoID
    video = pafy.new(url)
    print(video.title, video.thumb, video.duration, video.published)
    return([video.title, video.thumb, video.duration, video.published, username])

def getVideosFromPlayList(username, url):
    global userLikes, playlist
    
    playlist = pafy.get_playlist2(url)
    
    i = 0
    for video in playlist:
        userLikes[video.videoid] = [video.title, video.thumb, video.duration, username]
        print(video.videoid, video.title, video.thumb, video.duration, video.published, "\n")
        i += 1
    
    # save the list
    filename = 'likes_' + username.lower() + '.json'
    with open(filename, 'w') as fp:
        json.dump(userLikes, fp, indent=2)
    
    print("\n\nProcessed " + str(i) + " Youtube videos ...")

def processUserPlayList(filename, username):
    global userLikes
    
    with open(filename, encoding="utf8") as json_file: 
        mylikes = json.load(json_file)
    
    i = 1
    for record in mylikes:
        content = record['contentDetails']
        videoId = content['videoId']
        try:
            videoInfo = getYouTubeInfo2(videoId, username)
            userLikes[videoId] = videoInfo
            
            print(str(i) + ' Loaded Video Info: ' + videoId)
            i += 1
        except:
            print("Unable to load video: " + videoId)
    
    # save the list
    filename = 'likes_' + username.lower() + '.json'
    with open(filename, 'w') as fp:
        json.dump(userLikes, fp, indent=2)
    
    print("\n\nProcessed " + str(i) + " Youtube videos ...")
                
# run the script
if __name__ == '__main__':
    #loadUserPlayList('likes.json', 'Nathan')
    #getYouTubeInfo('MHBMBRSb7ZI')
    #getYouTubeInfo2('MHBMBRSb7ZI', 'Nathan')
    getYouTubeInfo2('a3PecWTHIAM', 'Nathan')
    #getVideosFromPlayList('Test', 'https://www.youtube.com/playlist?list=PLFD51ECAD4E496954')