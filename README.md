# DeckCastDJ
A Python based web app for sharing a barebones Youtube DJ Deck
## Introduction
With the world wide pandemic, I wanted a simple way to share the YouTube music I was playing with family/friends. So the obvious approach I tried was just use Zoom/Houseparty etc... and just share the computer audio. This worked ofcourse, but it required folks to use Zoom etc..., and it's hard for someone else to take over the "DJ" role.  Another approach I tried was to use VLC (https://www.videolan.org/vlc/index.html), or stream what I hear software (https://www.streamwhatyouhear.com/) so that family/friends lessoning just need a browser on their end. Well, if it wasn't for the need to change the ISP firewall settings for streaming to work, this would actually be a good approach. However, this requirement would be a none starter for most people; Firewall what, not to mention a security risk?

This got me thinking, what I wanted is a web app that someone can go to so they can see what Youtube video I watching. Well it turns out, such web apps already exist, but they are geared towards sharing videos, not music: For music sharing, you really want something like a DJ Deck (https://equipboard.com/posts/best-dj-software) where you can quickly switch between two music videos. More importantly, many of them don't work anymore, I guess they violated Google's policy at some point. Another downside of using this approach is the music is not streamed in realtime since no actually media content is passed to the listeners, rather just the information so that a user's Browser can load the correct video from Youtube. It's pretty much like everyone loads the Youtube video themselves, commericials and all. Not a big deal IMO, since this would not be a professional tool.

Now for the coding. Based on the requirements, I assumed this would require alot of coding, but surprisingly not so much. It turns out a workable solution can be put together using the YouTube Player API (https://developers.google.com/youtube/iframe_api_reference), Socketio (https://socket.io/), and Python/Flask (https://flask.palletsprojects.com/en/1.1.x/). And ofcourse, a Rasberry Pi 3 to host everything.  Thus far, it works well, it just needs a nicer UI, and the ability to allow multiple "DJs" at the same by implementing SocketIO "rooms". 
