# DeckCastDJ
A Python based web app for sharing a barebones Youtube DJ Deck

## Introduction
With the world wide pandemic, I wanted a simple way to share the YouTube music I was playing with family/friends. So the obvious approach I tried was just use Zoom/Houseparty etc... and just share the computer audio. This worked ofcourse, but it required folks to use Zoom etc..., and it's hard for someone else to take over the "DJ" role.  Another approach I tried was to use VLC (https://www.videolan.org/vlc/index.html), or stream what I hear software (https://www.streamwhatyouhear.com/) so that family/friends lessoning just need a browser on their end. Well, if it wasn't for the need to change the ISP firewall settings for streaming to work, this would actually be a good approach. However, this requirement would be a none starter for most people; Firewall what, not to mention a security risk?

This got me thinking, what I wanted is a web app that someone can go to so they can see what Youtube video I watching. Well it turns out, such web apps already exist, but they are geared towards sharing videos, not music: For music sharing, you really want something like a DJ Deck (https://equipboard.com/posts/best-dj-software) where you can quickly switch between two music videos. More importantly, many of them don't work anymore.  I am guessing they violated YouTube's policy at some point.  Additionally, using this approach means the music won't be streamed in "realtime", since no actually media content is streamed to the listeners. Information is transfered so that a listener's browser loads the correct video from Youtube, commericials and all. This is not a big deal IMO, since we are not talking about developing a professional tool. Also, testing across indicates there is only about a 2 second delay from what the "DJ" is playing to what listeners hear.

## Implementation
Based on the requirements, I assumed this would require quite a bit of coding, but surprisingly not. A workable, or rather proof of concept solution was implemented using the YouTube Player API (https://developers.google.com/youtube/iframe_api_reference), Socketio (https://socket.io/), and Python/Flask (https://flask.palletsprojects.com/en/1.1.x/). Everything was put together using less than 1000 lines of Python/HTML/Javascript code, with much of the time looking up how to glue everthing together. As such, I am sure someone with more experience can do it in half the amount of code and time. The whole stack, is ofcourse hosted on a Rasberry Pi3 running on my home network with a port exposed to the world. Thus far, the code works well, it just needs a nicer UI, maybe a chart box, and the ability to allow multiple "DJs" at the same by implementing SocketIO "rooms". One thing to keep in mind is that the webapp must be on a publicly facing domain name, otherwise many YouTube videos will not play. I ended up registering a new domain which redirects to my security camera's domain name, before being portforward to the Raspberry Pi3.

## Key Features
* Simultatenously play two YouTube Videos using a slider to control the volume level of each player.
* Cast what the "DJ" is playing to anyone who loads the webapp.
* A Searchable playlist containing all the videos that have been played.
* Processes and Loads a users YouTube "Like" list.

## TO-DOS
* A modern user interface instead if the current barebones interface.
* Implement "rooms" to allow multiple people to use the service.
* Simplify the process of loading videos. Currently the VideoID needs to be exracted from the YouTube URL.
* All for deletion of videos from the save list.
* Streamline the process of importing a users YouTube "likes". Current this is manual done on the backend. 
