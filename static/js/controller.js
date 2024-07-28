/*
 * Controller script to manage playback control of the YouTube 
 * iframe players.
 */

// 2. This code loads the IFrame Player API code asynchronously.
var tag = document.createElement('script');

tag.src = "https://www.youtube.com/iframe_api";
var firstScriptTag = document.getElementsByTagName('script')[0];
firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

// 3. This function creates an <iframe> (and YouTube player)
//    after the API code downloads.
var player1;
var player2;

// variable handel the mixer slider
var slider = document.getElementById("mixer");
var mixerOutput = document.getElementById("mixerValue");
mixerOutput.innerHTML = slider.value;

var playListOutput = document.getElementById("playList");
var queListOutput = document.getElementById("queList");
var vumeterOutput = document.getElementById("vumeter");

var connectedUsers = 0;

var currentVideoId1 = "";
var currentVideoId2 = "";
var clientId = "N/A";

// variable to see if to stop the mix
var playMix = false;
var stopMix = new Array();
var stopMixIndex = 0;

/**
 * Functions and variables here are for socketio
 */
var socket = io('http://' + document.domain + ':' + location.port);
var username = document.getElementById("uname").value
var messageText = document.getElementById("messageText");

socket.on('connect', function () {
  socket.emit('my event', {
    data: 'User Connected',
    uname: username
  })

  clientId = socket.io.engine.id;
})

socket.on('my response', function (msg) {
  // need to see if the message has a playlist?
  updatePlayList(msg);

  if (!getIsDJ() && !getIsPrivate()) {
    processMessage(msg);
  }

  // see if to update the user count
  if (msg.data.includes("User Connected")) {
    updateConnectedUsers(msg);
  } 

  console.log(msg);
  messageText.innerHTML = msg.data + " (Users: " + connectedUsers + ")";
})

// update the que list or playList if needed
function updatePlayList(msg) {
  console.log("Updating que/playlist: " + msg);

  // display the que list if needed
  if ("queListHTML" in msg) {
    queClientId = msg.clientId;

    if (queClientId === clientId) {
      queListOutput.innerHTML = msg.queListHTML;
    } else {
      console.log("Que Client Id doesn't match ...");
    }
  }

  // display the playlist html if needed
  if ("playListHTML" in msg) {
    playListOutput.innerHTML = msg.playListHTML;
  }

  // see if to alert the user that the video was saved
  if ("savedVideo" in msg) {
    alert(msg.savedVideo);
  }
}

// clear the playList
function clearPlayList() {
  playListOutput.innerHTML = "";
}

// process messages
function processMessage(msg) {
  var msgText = msg.data;

  if (msgText.includes("Mixer")) {
    updateMixerValue(msg);
  } else if (msgText.includes("Video Changed")) {
    updateVideo(msg);
  } else if (msgText.includes("State Changed")) {
    updatePlayerState(msg);
  } else if (msgText.includes("Speed Changed")) {
    updatePlaybackSpeed(msg);
  } else {
    console.log("Unhandeled socket io message: " + msgText);
  }
}

// function to restart the current video from the beginning
// update the players speed
function restartVideo(playerNum) {
  var currentPlayer;

  if (playerNum == 1) {
    currentPlayer = player1;
  } else {
    currentPlayer = player2;
  }

  currentPlayer.seekTo(0);
}

// function to process a message to update the playback speed
// for a particular player1
function updatePlaybackSpeed(msg) {
  var playerNum = msg.player;
  var playerSpeed = msg.speed;

  // update the UI radio buttons?
  if (playerNum == 1) {
    radiobtns = document.getElementsByName("speedA");
    radiobtns[playerSpeed].checked = true;
  } else {
    radiobtns = document.getElementsByName("speedB");
    radiobtns[playerSpeed].checked = true;
  }

  // update the actual player
  updatePlayerSpeed(playerNum, playerSpeed);
}

// This is by the speed radio buttons
function onChangeSpeed(playerNum, speedButton) {
  var playerSpeed = speedButton.value;
  updatePlayerSpeed(playerNum, playerSpeed);
}

// update the players speed
function updatePlayerSpeed(playerNum, playerSpeed) {
  var currentPlayer;

  if (playerNum == 1) {
    currentPlayer = player1;
  } else {
    currentPlayer = player2;
  }

  // update the playback speed
  if (playerSpeed == 0) {
    currentPlayer.setPlaybackRate(1.00);
  } else if (playerSpeed == 1) {
    currentPlayer.setPlaybackRate(1.05);
  } else if (playerSpeed == 2) {
    currentPlayer.setPlaybackRate(1.10);
  } else if (playerSpeed == 3) {
    currentPlayer.setPlaybackRate(1.15);
  } else if (playerSpeed == 4) {
    currentPlayer.setPlaybackRate(1.20);
  } else {
    currentPlayer.setPlaybackRate(1.00);
  }

  // send a message to server if I am the DJ
  if (getIsDJ()) {
    jsonText = {
      data: 'Speed Changed',
      player: playerNum,
      speed: playerSpeed
    }
    socket.emit('my event', jsonText);
  }

  console.log("Playback Speed > " + playerNum + " : " + playerSpeed);
}

// update the mixer value, and hence the player volumes
function updateMixerValue(msg) {
  console.log("Updating mixer setting ...");

  var mixRatio = Number(msg.mixer);
  slider.value = mixRatio;
  player1.setVolume(msg.volume1);
  player2.setVolume(msg.volume2);

  mixerOutput.innerHTML = mixRatio + " || Volume Player 1 @ " + msg.volume1 + "% / Player 2 @ " + msg.volume2 + "%";
}

// change the video in the player
function updateVideo(msg) {
  console.log("Changing video ...");

  var playerNum = msg.player;
  var videoId = msg.videoId;

  if (playerNum == 1) {
    player1.loadVideoById(videoId, 0);
  } else {
    player2.loadVideoById(videoId, 0);
  }
}

function updatePlayerState(msg) {
  var playerNum = msg.player;
  var playerState = msg.state;
  var playerTime = msg.ctime;

  var player;
  if (playerNum == 1) {
    player = player1;
  } else {
    player = player2;
  }

  if (playerState == YT.PlayerState.PLAYING) {
    player.seekTo(playerTime, true);
    player.playVideo();
  } else if (playerState == YT.PlayerState.PAUSED) {
    player.pauseVideo();
  } else {
    console.log("Ignoring state change: " + playerState);
  }
}

function updateConnectedUsers(msg) {
  if (!getIsDJ()) {
    if ("videoId1" in msg) {
      currentVideoId1 = msg.videoId1;
    }

    if ("videoId2" in msg) {
      currentVideoId2 = msg.videoId2;
    }
  }

  connectedUsers = msg.userCount;
}

function onYouTubeIframeAPIReady() {
  var videoId1 = "1w4O-eiUrZ8"
  var videoId2 = "J3VrtjFIy7U"

  player1 = new YT.Player('player1', {
    height: '340',
    width: '560',
    videoId: videoId1,
    events: {
      'onReady': onPlayer1Ready,
      'onStateChange': onPlayer1StateChange
    }
  });

  player2 = new YT.Player('player2', {
    height: '340',
    width: '560',
    videoId: videoId2,
    events: {
      'onReady': onPlayer2Ready,
      'onStateChange': onPlayer2StateChange
    }
  });
}

// 4. The API will call this function when the video player is ready.
function onPlayer1Ready(event) {
  if (currentVideoId1) {
    event.target.loadVideoById(currentVideoId1);
  }

  event.target.setVolume(100);
  event.target.playVideo();
}

// 4. The API will call this function when the video player is ready.
function onPlayer2Ready(event) {
  if (currentVideoId2) {
    event.target.loadVideoById(currentVideoId2);
  }

  event.target.setVolume(100);
  event.target.playVideo();
}

// 5. The API calls this function when the player's state changes.
//    The function indicates that when playing a video (state=1),
//    the player should play for six seconds and then stop.
function onPlayer1StateChange(event) {
  var isDJ = getIsDJ();

  if (isDJ) {
    currentTime = player1.getCurrentTime();
    jsonText = {
      data: 'Player 1 State Changed',
      player: 1,
      state: event.data,
      ctime: currentTime
    }

    socket.emit('my event', jsonText)
  } else if (event.data == YT.PlayerState.PLAYING) {
    videoId = player1.getVideoData()['video_id'];
    currentTime = Math.floor(player1.getCurrentTime());
    setCurrentVideoPlaying(1, videoId, currentTime);
  }

  console.log("Player1 State " + event.data + ", DJ: " + isDJ);
}

// 5. The API calls this function when the player's state changes.
//    The function indicates that when playing a video (state=1),
//    the player should play for six seconds and then stop.
function onPlayer2StateChange(event) {
  var isDJ = getIsDJ();

  if (isDJ) {
    currentTime = player2.getCurrentTime();
    jsonText = {
      data: 'Player 2 State Changed',
      player: 2,
      state: event.data,
      ctime: currentTime
    }

    socket.emit('my event', jsonText)
  } else if (event.data == YT.PlayerState.PLAYING) {
    videoId = player2.getVideoData()['video_id'];
    currentTime = Math.floor(player2.getCurrentTime());
    setCurrentVideoPlaying(2, videoId, currentTime);
  }

  console.log("Player2 State " + event.data + ", DJ: " + isDJ);
}

// function to set the current video that's playing
function setCurrentVideoPlaying(playerNum, videoId, currentTime) {
  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'Current Video',
    pin: pin,
    clientId: clientId,
    player: playerNum,
    videoId: videoId,
    ctime: currentTime
  }
  socket.emit('my event', jsonText);

  console.log("Current Video, Player # " + playerNum + ", VideoID " + videoId);
}

// functon called by load video button
function loadVideo(playerNum) {
  var input;
  if (playerNum == 1) {
    input = document.getElementById("videoId1");
  } else {
    input = document.getElementById("videoId2");
  }

  var videoId = getYouTubeID(input.value);
  loadVideoForPlayer(playerNum, videoId);
  input.value = "";
}

// functon called by que video button
function queVideo(playerNum) {
  var input;

  if (playerNum == 1) {
    input = document.getElementById("videoId1");
  } else {
    input = document.getElementById("videoId2");
  }

  var videoId = getYouTubeID(input.value);
  addToQueList(videoId);
  input.value = "";
}

// functon to save the loaded video to the saved playlist if the correct
// username is provided
function saveVideo(playerNum) {
  var videoId;
  var username = document.getElementById("uname").value;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
  } else {
    videoId = player2.getVideoData()['video_id'];
  }

  jsonText = {
    data: 'Video Saved',
    uname: username,
    videoId: videoId
  }
  socket.emit('my event', jsonText);

  console.log(playerNum + " Save Video ID: " + videoId);
}

// functon to add a tracklist to the loaded video
function addTrackList(playerNum) {
  var pin = document.getElementById("pin").value;
  var videoId;
  var title;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
    title = player1.getVideoData()['title'];
  } else {
    videoId = player2.getVideoData()['video_id'];
    title = player2.getVideoData()['title'];
  }

  var myWindow = window.open("", "TrackListWindow", "width=400,height=625");
  myWindow.document.write("<h4>Track List: " + title + "</h4>");
  myWindow.document.write("<textarea id=\"tracklist\" name=\"tracklist\" rows=\"35\" cols=\"50\"></textarea>")

  // before window close grab the text in the text area
  myWindow.onbeforeunload = function () {
    var tracklist = myWindow.document.getElementById("tracklist").value;

    jsonText = {
      data: 'Add TrackList',
      pin: pin,
      videoId: videoId,
      tracklist: tracklist
    }
    socket.emit('my event', jsonText);

    console.log("Window closed: " + videoId + "\n" + tracklist);
  }

  console.log(playerNum + " Track List For Video ID: " + videoId + "\n" + title);
}

// function to add a youtube playlist to the que.
function addPlayListToQueList() {
  var playlistUrl = document.getElementById("filter").value;

  if(playlistUrl.includes('https://www.youtube.com/playlist?list=')) {
    addToQueList(playlistUrl);
    queListOutput.innerHTML = getLoadingText();
    document.getElementById("filter").value = "";
  } else {
    alert("Invalid YouTube Playlist Url: " + playlistUrl);
  }
}

// function to send a video id to be added to the que list
function addToQueList(videoId) {
  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'Video Qued',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: videoId
  }
  socket.emit('my event', jsonText);
  console.log("Video Qued: " + videoId + " " + clientId);
}

// function to add a saved playlist to the que
function queSavedPlayList() {
  var pin = document.getElementById("pin").value;
  username = document.getElementById("uname").value;
  var savedList = 'savedList:' + username;

  jsonText = {
    data: 'Video Qued',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: savedList 
  }
  socket.emit('my event', jsonText);

  // clear the playlist stream line interface
  clearPlayList()

  console.log("Saved Playlist Qued: " + savedList + " " + clientId);
}

// function to play the videos in the que list
function playQueList(queListString) {
  var queList = queListString.split(",");
  player1.loadPlaylist(queList);

  console.log("Playing qued videos: " + queListString + " / " + queList[0]);
}

// function to mix the que list
async function mixQueList(queListString, queListTimesString) {
  var queList = queListString.split(",");
  var timeList = queListTimesString.split(",");
  var startVideo = document.getElementById("startMixAt").value - 1;
  var overlap = document.getElementById("mixOverlap").value;
  var playPercent = document.getElementById("mixPlayPercent").value;
  var startTime = 0;
  var endTime = 0;

  // set the stop mix index by making a copy of global variable so we don't have 2 mixes going at once
  let stopIndex = stopMixIndex;
  stopMix[stopIndex] = false;
  playMix = true;

  // check the start video value is valid
  if(startVideo < 0 || startVideo == queList.length) {
    startVideo = 0;
  }

  for (let i = startVideo; i < queList.length; i++) {
    var endTime = Math.round(timeList[i] * (playPercent / 100));
    var playTime = endTime - overlap;
    var trackNum = i + 1

    // check to make sure we at least play 30 seconds of video
    if (playTime < 30) playTime = 30;

    console.log("Mix Video: " + trackNum + " / " + " : " + timeList[i]);
    console.log("Play Time: " + playTime + " : Endtime: " + endTime);

    // display current mixing information
    var message = "Curent Mix Track: " + trackNum + " / Playtime: " + toHHMMSS(playTime);
    document.getElementById("queMixMessage").innerHTML = message;

    // if it's not the first video set the start time to the overlap
    if (i != startVideo) {
      startTime = overlap;
    }

    // see which player to use
    if (i % 2 == 0) {
      console.log("Player 1 ...");
      // move the slide to player 1
      moveSlideTo(1);

      player1.loadVideoById(queList[i], startTime);
      player1.playVideo();

    } else {
      console.log("Player 2 ...");
      moveSlideTo(2);

      player2.loadVideoById(queList[i], startTime);
      player2.playVideo();
    }

    // load the vumeter gif
    //vumeterOutput.innerHTML = "<img src=\"https://u-he.com/products/satin/assets/images/uhe-satin-animation-vumeters.gif\" alt=\"VU Meter\">";
    //vumeterOutput.innerHTML = "<img src=\"https://shopjustaudio.com/cdn/shop/products/VU-Digital-x300.gif?v=1671301051&width=300\" alt=\"VU Meter\">";
    vumeterOutput.innerHTML = "<img src=\"https://i.pinimg.com/originals/f7/20/df/f720df37ff3964df6e4b92146a1fe97e.gif\" alt=\"VU Meter\"  width=\"550\" height=\"384\">";

    // delay a specified number of seconds, playtime, before next video is played  
    await wait(playTime * 1000);

    // check to see if to top the mix
    if (stopMix[stopIndex]) {
      console.log("Stopping Mix @ " + i);
      break;
    }
  }

  playMix = false;
  vumeterOutput.innerHTML = "";
  console.log("Mix Play Done ...");
}

// function to wait a certian time before doing something
function wait(timeout) {
  return new Promise((resolve) => setTimeout(resolve, timeout));
}

// function to clear videos on the que list
function clearQueList() {
  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'Video Qued',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: "0"
  }
  socket.emit('my event', jsonText);
  console.log("Clear Video Que: " + clientId);

  if(playMix) {
    stopMixPlay();
  }
}

// function to stop a playing mix
function stopMixPlay() {
  stopMix[stopMixIndex] = true;
  stopMixIndex++;

  player1.pauseVideo();
  player2.pauseVideo();

  // hide the vumeter
  vumeterOutput.innerHTML = "";

  slider.value = 50;
  changePlayerVolume(50);
}

// function to return time as hh:mm:ss given seconds
// https://stackoverflow.com/questions/1322732/convert-seconds-to-hh-mm-ss-with-javascript
function toHHMMSS(seconds) {
  const date = new Date(null);
  date.setSeconds(seconds);
  return date.toISOString().slice(11, 19);
}

// function to get text to show to user when loading videos
function getLoadingText() {
  var loadingText = "<div style=\"text-align: center\">" +
    "<h4>Loading Youtube Playlist ...</h4>" +
    "<img src=\"https://i.gifer.com/YCZH.gif\" align=\"center\">" +
    " </div>";

  return loadingText;
}

// function to merge a playlist to the default playlist
function mergePlayList() {
  username = document.getElementById("uname").value;
  var playlistUrl = document.getElementById("filter").value;

  if(playlistUrl.includes('https://www.youtube.com/playlist?list=')) {
    jsonText = {
      data: 'Merge PlayList',
      uname: username,
      filter: playlistUrl
    }
    socket.emit('my event', jsonText);

    // clear the filter text input
    document.getElementById("filter").value = "";
    console.log("Merged YouTube PlayList: " + playlistUrl);
  } else {
    alert("Invalid YouTube Playlist Url: " + playlistUrl);
  }
}

// extract the youtube ID from url
// https://stackoverflow.com/questions/3452546/how-do-i-get-the-youtube-video-id-from-a-url
function getYouTubeID(url) {
  if (url.length == 11) {
    return url;
  } else {
    var regExp = /^.*((youtu.be\/)|(v\/)|(\/u\/\w\/)|(embed\/)|(watch\?))\??v?=?([^#&?]*).*/;
    var match = url.match(regExp);
    return (match && match[7].length == 11) ? match[7] : false;
  }
}

// load a video given the videoNum and videoID
function loadVideoForPlayer(playerNum, videoId) {
  username = document.getElementById("uname").value;

  if (playerNum == 1) {
    player1.loadVideoById(videoId, 0);

    if (getIsDJ()) {
      jsonText = {
        data: 'Video Changed -- Player 1',
        player: 1,
        uname: username,
        videoId: videoId
      }
      socket.emit('my event', jsonText);
    }
  } else {
    player2.loadVideoById(videoId, 0);

    if (getIsDJ()) {
      jsonText = {
        data: 'Video Changed -- Player 2',
        player: 2,
        uname: username,
        videoId: videoId
      }
      socket.emit('my event', jsonText);
    }
  }
}

// function to explicity load the playlist from server
function loadPlayList() {
  username = document.getElementById("uname").value;
  var filter = document.getElementById("filter").value;
  loadPlayListForUser(username, filter);
}

// function to load a playlist for a user or youtube playlist linked to user
function loadPlayListForUser(username, filter, showUsername = false) {
  jsonText = {
    data: 'Load PlayList',
    uname: username,
    filter: filter
  }
  socket.emit('my event', jsonText);

  // if we loaded a youtube playlist clear filter input
  if (filter.startsWith("https")) {
    playListOutput.innerHTML = getLoadingText();
    document.getElementById("filter").value = "";
  }

  // show the user name if we used one of the buttons to load a playList
  if (showUsername) {
    document.getElementById("uname").value = username;
  }

  console.log('Loading playlist ... ' + username);
}

// function to remove a video from the playlist
function removeVideoFromList(videoId) {
  if (confirm('Really Delete ( ' + videoId + ' ) From Playlist?')) {
    username = document.getElementById("uname").value;

    jsonText = {
      data: 'Delete Video',
      uname: username,
      videoId: videoId
    }
    socket.emit('my event', jsonText);
  } else {
    console.log('Deletion cancelled ... ' + videoId);
  }
}

// function to remove a video from the que playlist
function removeVideoFromQueList(videoId) {
  var pin = document.getElementById("pin").value;
  username = document.getElementById("uname").value;

  jsonText = {
    data: 'Delete Qued Video',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: videoId
  }

  socket.emit('my event', jsonText);
  console.log('Delete From Que List: ' + videoId);
}

// return if a person is the DJ or not
function getIsDJ() {
  return document.getElementById("isDJ").checked;
}

// return if the person wants to be private
function getIsPrivate() {
  return document.getElementById("isPrivate").checked;
}

// reset data on the server
function resetValues() {
  connectedUsers = 1;

  jsonText = {
    data: 'RESET',
    uname: username
  }
  socket.emit('my event', jsonText);

  console.log('Reseting server values ...' + uname);
}

// add function to "smoothly" move the slider using a timer function
function moveSlideTo(playerNum) {
  var steps = 200;
  var mixStart = Number(slider.value);
  var mixStep = 0;
  var mixRatio = 0;

  if (playerNum == 1) {
    mixStep = mixStart / steps;
  } else {
    mixStep = (100 - mixStart) / steps;
  }

  for (let i = 1; i < (steps + 1); i++) {
    setTimeout(function timer() {
      if (playerNum == 1) {
        mixRatio = Math.round(mixStart - mixStep * i)
      } else {
        mixRatio = Math.round(mixStart + mixStep * i)
      }

      // move the slider
      slider.value = mixRatio
      changePlayerVolume(mixRatio);

      //console.log(i + ": delay for " + playerNum + " mixer: " + mixStart + " / " + mixStep + " / " + mixRatio);
    }, i * 20);
  }
}

// function to change the players volume base on the slider position
function changePlayerVolume(mixRatio) {
  var player1Vol = 0;
  var player2Vol = 0;

  if (mixRatio <= 50) {
    player1Vol = 100;
    player2Vol = mixRatio * 2;
  } else {
    player1Vol = (100 - mixRatio) * 2;
    player2Vol = 100;
  }

  player1.setVolume(player1Vol);
  player2.setVolume(player2Vol);

  mixerOutput.innerHTML = mixRatio + " || Volume Player 1 @ " + player1Vol + "% / Player 2 @ " + player2Vol + "%";

  if (getIsDJ()) {
    jsonText = {
      data: 'Mixer Ratio Changed',
      volume1: player1Vol,
      volume2: player2Vol,
      mixer: mixRatio
    }

    socket.emit('my event', jsonText)
  }
}

// add a function to handle slider events
slider.oninput = function () {
  var mixRatio = Number(this.value);
  changePlayerVolume(mixRatio);
}    