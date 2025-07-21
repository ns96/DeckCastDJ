# DeckCastDJ
A Python/Flask based webapp for sharing YouTube music videos using basic DJ Deck

## Introduction
With the world wide pandemic, I wanted a simple way to share the YouTube music I was playing with family/friends. The obvious approach I tried was just use Zoom/Houseparty etc... and just share the computer audio. This worked ofcourse, but it required folks to use Zoom etc..., and getting good audio quality was a challenge.  Another approach I tried was to use VLC (https://www.videolan.org/vlc/index.html), or Stream What I hear software (https://www.streamwhatyouhear.com/) so that family/friends lessoning just needed a browser on their end. A big downside to this approach is the need to change the ISP firewall settings for streaming to work. Unfortunately, most people don't known how to go about doing this, not to mention the security risk in doing so.

This got me thinking, what I wanted is a web app that someone can go to so they can see what Youtube video I watching. Well it turns out, such web apps already exist, but they are geared towards sharing videos, not music: For music sharing, you really want something like a DJ Deck (https://equipboard.com/posts/best-dj-software) where you can quickly switch between two music videos. More importantly, many of those sites don't work anymore.  I am guessing they violated YouTube's policy at some point.  Additionally, using this approach means music won't be streamed in "realtime", since no actually media content is streamed to the listeners. All that's transfered is data so that a listener's browser loads the correct video from Youtube, commercials and all. This is not a big deal IMO, since we are not talking about developing a professional tool.  Also, testing indicates there is only about a 2 second delay from what the "DJ" is playing to what listeners hears.

## Implementation
Based on the requirements, a workable webapp was built using the YouTube Player API (https://developers.google.com/youtube/iframe_api_reference), Socketio (https://socket.io/), and Python/Flask (https://flask.palletsprojects.com/en/1.1.x/). Everything is put together using less than 1500 lines of Python/HTML/Javascript code, with much of the time looking up how to glue everthing together. The whole stack, was originally hosted on a Rasberry Pi3 running on my home network with a port exposed to the world. Thus far, the code works well, it just needs a nicer UI, maybe a chart box, and the ability to allow multiple "DJs" at the same time by implementing SocketIO "rooms". One thing to keep in mind is that the webapp must be on a publicly facing domain name, otherwise many YouTube videos will not play. I ended up registering a new domain which portforward to the Raspberry Pi3.

## Key Features
* Simultatenously play two YouTube Videos using a slider to control the volume level of each player.
* Cast what the "DJ" is playing to anyone who loads the webapp.
* A Searchable playlist containing all the videos that have been played.
* Loads videos from any public YouTube playlist.
* Ability to create a Que video list and play all back to back
* Basic Automixing of Qued Videos
* Basic pitch control
* Basic mobile interface at http://the.server.ip.addr:5054/mobile 

## TO-DOS
* A modern user interface instead if the current barebones interface.
* Implement "rooms" to allow multiple people to use the service.
* Improve Automixng functionality
* Clean up Python code 
* Move away from using pafy which has issues working with the current YouTube API (https://stackoverflow.com/questions/70344739/backend-youtube-dl-py-line-54-in-fetch-basic-self-dislikes-self-ydl-info)
* Make this a more user friendly app that none programmers can deploy, maybe use a VM appliance?
