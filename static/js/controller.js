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

// keep track of the player state to see if video actually played
var player1State = -2;
var player2State = -2;
var notPlayedCount = 0;

// variables to handel the mixer slider
var mixerSlider = document.getElementById("mixer");
var mixerOutput = document.getElementById("mixerValue");
mixerOutput.innerHTML = mixerSlider.value;

var playListOutput = document.getElementById("playList");
var queListOutput = document.getElementById("queList");
var vumeterOutput = document.getElementById("vumeter");

var connectedUsers = 0;

var currentVideoId1 = "1w4O-eiUrZ8";
var currentVideoId2 = "J3VrtjFIy7U";
var clientId = "N/A";

// variable to see if to stop the mix
var playMix = false;
var stopMix = new Array();
var stopMixIndex = 0;

// used to indicate that a playlist is being loaded
var loadingPlaylist = false;

// active tracklists for the dynamic scrolling marquee
var trackList1 = null;
var trackList2 = null;

// indicate if we are on a mobile device
var mobile = false;

// keep track of the current track number for the video playing. Used to display current track info
var trackNum = -1;

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

  if (currentVideoId1) {
    socket.emit('my event', {
      data: 'Get TrackList Only',
      playerNum: 1,
      videoId: currentVideoId1,
      clientId: clientId
    });
  }
  if (currentVideoId2) {
    socket.emit('my event', {
      data: 'Get TrackList Only',
      playerNum: 2,
      videoId: currentVideoId2,
      clientId: clientId
    });
  }
})

socket.on('my response', function (msg) {
  // need to see if the message has a playlist?
  updatePlayList(msg);

  if (!getIsDJ() && !isPrivate()) {
    processMessage(msg);
  }

  // see if to update the user count
  if (msg.data.includes("User Connected")) {
    updateConnectedUsers(msg);
  }

  // see if to show the tracklist edit dialog
  if (msg.data.includes("Edit TrackList")) {
    if (msg.clientId === clientId) {
      addOrEditTrackList(msg);
    }
  }

  // see if to show the bookmarks dialog
  if (msg.data.includes("View Bookmarks")) {
    if (msg.clientId === clientId) {
      selectOrEditBookmarks(msg);
    }
  }

  // see if to show the track numbers dialog
  if (msg.data.includes("View Track Numbers")) {
    if (msg.clientId === clientId) {
      showTrackNumbersDialog(msg);
    }
  }

  // handle extract track numbers events
  if (msg.data.includes("Extract Track Numbers Done")) {
    if (msg.clientId === clientId) {
      handleExtractTrackNumbersDone(msg);
    }
  }

  if (msg.data.includes("Extract Track Numbers Error")) {
    if (msg.clientId === clientId) {
      handleExtractTrackNumbersError(msg);
    }
  }

  // handle delete track numbers events
  if (msg.data.includes("Delete Track Numbers Done")) {
    if (msg.clientId === clientId) {
      showTrackNumbersDialog(msg);
    }
  }

  if (msg.data.includes("Get Progress")) {
    updateLoadingProgress(msg);
  }

  if (msg.data.includes("Check Video Status")) {
    showVideoStatus(msg);
  }

  // see if to update active tracklists for marquee
  if (msg.data.includes("Current Video")) {
    if (msg.clientId === clientId) {
      handleCurrentVideoTracklistUpdate(msg);
    }
  }

  // see if to update active tracklists privately for marquee
  if (msg.data.includes("Get TrackList Only")) {
    handleTracklistOnlyResponse(msg);
  }

  console.log(msg);
  messageText.innerHTML = msg.data + " (Users: " + connectedUsers + ")";
})

// update the que list or playList if needed
function updatePlayList(msg) {
  console.log("Updating que/playlist: " + msg);
  var msgClientId;

  // display the que list if needed
  if ("queListHTML" in msg) {
    loadingPlaylist = false;
    msgClientId = msg.clientId;

    if (msgClientId === clientId) {
      if (queListOutput) {
        queListOutput.innerHTML = msg.queListHTML;
      }
    } else {
      console.log("Que Client Id doesn't match ...");
    }
  }

  // display the playlist html if needed
  if ("playListHTML" in msg) {
    loadingPlaylist = false;
    msgClientId = msg.clientId;

    if (msgClientId === clientId) {
      if (playListOutput) {
        playListOutput.innerHTML = msg.playListHTML;
      }
    }
  }

  // see if to alert the user that the video was saved
  if ("savedVideo" in msg) {
    showVideoStatus(msg);
    alert(msg.savedVideo);
  }

  updateSelectedStars();
}

// clear the playList
function clearPlayList() {
  if (playListOutput) {
    playListOutput.innerHTML = "";
  }
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
  mixerSlider.value = mixRatio;
  player1.setVolume(msg.volume1);
  player2.setVolume(msg.volume2);

  mixerOutput.innerHTML = mixRatio + " || Volume Player 1 @ " + msg.volume1 + "% / Player 2 @ " + msg.volume2 + "%";
}

// change the video in the player
function updateVideo(msg) {
  console.log("Changing video ...");

  var playerNum = msg.player;
  var videoId = msg.videoId;

  // store global variable for track number
  trackNum = msg.trackNum;

  if (playerNum == 1) {
    currentVideoId1 = videoId;
    player1.loadVideoById(videoId, 0);
  } else {
    currentVideoId2 = videoId;
    player2.loadVideoById(videoId, 0);
  }

  updateSelectedStars();
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
  if (!getIsDJ() && !isPrivate()) {
    if ("videoId1" in msg) {
      currentVideoId1 = msg.videoId1;
    }

    if ("videoId2" in msg) {
      currentVideoId2 = msg.videoId2;
    }
  }

  connectedUsers = msg.userCount;
  updateSelectedStars();
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

  if (event.data == YT.PlayerState.PLAYING) {
    var activeId = player1.getVideoData()['video_id'];
    if (activeId && activeId !== currentVideoId1) {
      currentVideoId1 = activeId;
      updateSelectedStars();
    }
  } else if (event.data == YT.PlayerState.ENDED) {
    currentVideoId1 = "";
    updateSelectedStars();
  }

  player1State = event.data;
  console.log("Player1 State " + player1State + ", DJ: " + isDJ);
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

  if (event.data == YT.PlayerState.PLAYING) {
    var activeId = player2.getVideoData()['video_id'];
    if (activeId && activeId !== currentVideoId2) {
      currentVideoId2 = activeId;
      updateSelectedStars();
    }
  } else if (event.data == YT.PlayerState.ENDED) {
    currentVideoId2 = "";
    updateSelectedStars();
  }

  player2State = event.data;
  console.log("Player2 State " + player2State + ", DJ: " + isDJ);
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
    ctime: currentTime,
    trackNum: trackNum
  }
  socket.emit('my event', jsonText);

  console.log("Current Video, Player # " + playerNum + ", VideoID " + videoId);
}

// add event listeners for videoId input textfields if they exist in the DOM (desktop only)
var videoId1Input = document.getElementById("videoId1");
if (videoId1Input) {
  videoId1Input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault(); // Prevent the default action of the Enter key
      loadVideo(1);
    }
  });
}

var videoId2Input = document.getElementById("videoId2");
if (videoId2Input) {
  videoId2Input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault(); // Prevent the default action of the Enter key
      loadVideo(2);
    }
  });
}

// function to load a video from mobile device
function loadVideoForMobile(playerNum) {
  mobile = true;
  loadVideo(playerNum);
}

// functon called by load video button
function loadVideo(playerNum) {
  var input;

  if (mobile) {
    input = document.getElementById("filter");
  } else if (playerNum == 1) {
    input = document.getElementById("videoId1");
  } else {
    input = document.getElementById("videoId2");
  }

  var videoId = getYouTubeID(input.value);
  loadVideoForPlayer(playerNum, videoId);
  input.value = "";
}

// function to start video after short delay. This is to allow playback even when screen is locked
async function delayPlay(playerNum) {
  var currentPlayer;
  var currentVideoId;
  var DELAY = 6;
  var button;

  if (playerNum == 1) {
    currentPlayer = player1;
    currentVideoId = currentVideoId1;
    button = document.getElementById("dp1");
  } else {
    currentPlayer = player2;
    currentVideoId = currentVideoId2;
    button = document.getElementById("dp2");
  }

  // change button color to red
  button.style.backgroundColor = "red";

  // delay a specified number of seconds, playtime, before playing video  
  await wait(DELAY * 1000);
  currentPlayer.loadVideoById(currentVideoId, 0);
  currentPlayer.playVideo();

  // change button color back to green
  button.style.backgroundColor = "green";

  console.log("Current Video, Player # " + playerNum + ", Started after " + DELAY + "s delay ...");
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
    videoId: videoId,
    player: playerNum
  }
  socket.emit('my event', jsonText);

  console.log(playerNum + " Save Video ID: " + videoId);
}



// socket event response when track extraction finishes successfully
function handleExtractTrackNumbersDone(msg) {
  var overlay = document.getElementById("app-dialog-overlay");
  if (overlay) {
    var methodInput = document.getElementById("tn-method");
    if (methodInput) {
      // Re-render the modal with the new tracks
      showTrackNumbersDialog(msg);
      return;
    }
  }
}

// socket event response when track extraction fails
function handleExtractTrackNumbersError(msg) {
  var overlay = document.getElementById("app-dialog-overlay");
  if (overlay) {
    var extractBtn = document.getElementById("tn-extract-btn");
    var statusDiv = document.getElementById("tn-status");
    var methodSelect = document.getElementById("tn-method");
    if (extractBtn && statusDiv && methodSelect) {
      extractBtn.disabled = false;
      statusDiv.style.display = "none";
      methodSelect.disabled = false;
      overlay.querySelectorAll("input").forEach(function(input) { input.disabled = false; });
    }
  }
  alert("Error extracting tracks: " + msg.message);
}

// function to add a bookmark for the current video and time
function addBookmarkForPlayer(playerNum) {
  var videoId;
  var time;
  username = document.getElementById("uname").value;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
    time = player1.getCurrentTime();
  } else {
    videoId = player2.getVideoData()['video_id'];
    time = player2.getCurrentTime();
  }

  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'Add Bookmark',
    pin: pin,
    clientId: clientId,
    videoId: videoId,
    uname: username,
    time: time
  }
  socket.emit('my event', jsonText);

  console.log("Add Bookmark, Player # " + playerNum + ", VideoID " + videoId + ", Time: " + time);
}

// function to add a youtube playlist to the que.
function addPlayListToQueList() {
  var playlistUrl = document.getElementById("filter").value;

  if (playlistUrl.includes('https://www.youtube.com/playlist?list=')) {
    addToQueList(playlistUrl);
    if (queListOutput) {
      queListOutput.innerHTML = getLoadingText();
    }
    document.getElementById("filter").value = "";

    getLoadingProgress(playlistUrl, 2);
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
  var filterText = document.getElementById("filter").value;

  var savedList = 'savedList:' + username

  jsonText = {
    data: 'Video Qued',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: savedList,
    filter: filterText
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

// function to call mixque list funtion to atomatically check that the videos is list are playable
function checkQueList() {
  document.getElementById("mixPlayPercent").value = -1;

  // get the mixQueList button and click it
  document.getElementById("mixButton").click();
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
  if (startVideo < 0 || startVideo == queList.length) {
    startVideo = 0;
  }

  for (let i = startVideo; i < queList.length; i++) {
    var videoId = queList[i];
    var endTime = Math.round(timeList[i] * (playPercent / 100));
    var playTime = endTime - overlap;
    trackNum = i + 1

    // check to make sure we at least play 30 seconds of video 
    // or 5 seconds if doing play testing by setting play percent to -1 
    if (playPercent == -1) {
      playTime = 5;
    } else if (playPercent == -2) {
      // we are playing audio to make animated gifs so 
      // start 1.5 minutes in and only play 25 seconds
      playTime = 25;
      startTime = 90;
      overlap = startTime;
    } else if (playTime < 30) {
      playTime = 30;
    }

    console.log("Mix Video: " + trackNum + " / " + " : " + timeList[i]);
    console.log("Play Time: " + playTime + " : Endtime: " + endTime);

    // display current mixing information
    var message = "Curent Mix Track: " + trackNum + " / Playtime: " + toHHMMSS(playTime);

    if (playPercent == -1) {
      message = message + " || Testing (Invalid Videos: " + notPlayedCount + ")";
    }

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

      player1.loadVideoById(videoId, startTime);
      player1.playVideo();

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
      console.log("Player 2 ...");
      moveSlideTo(2);

      player2.loadVideoById(videoId, startTime);
      player2.playVideo();

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

    // load the animated vumeter or tape gif
    //vumeterOutput.innerHTML = "<img src=\"https://u-he.com/products/satin/assets/images/uhe-satin-animation-vumeters.gif\" alt=\"VU Meter\">";
    //vumeterOutput.innerHTML = "<img src=\"https://shopjustaudio.com/cdn/shop/products/VU-Digital-x300.gif?v=1671301051&width=300\" alt=\"VU Meter\">";
    if (vumeterOutput) {
      vumeterOutput.innerHTML = "<img src=\"https://i.pinimg.com/originals/f7/20/df/f720df37ff3964df6e4b92146a1fe97e.gif\" alt=\"VU Meter\"  width=\"440\" height=\"307\">";
    }
    //vumeterOutput.innerHTML = "<img src=\"https://bestgifsdotnet.wordpress.com/wp-content/uploads/2013/12/cassette.gif\" alt=\"VU Meter\">";

    // delay a specified number of seconds, playtime, before next video is played  
    await wait(playTime * 1000);

    // if we doing playing test on video check the player state
    if (playPercent == -1) {
      if (i % 2 == 0) {
        setVideoPlayed(player1State, videoId);
      } else {
        setVideoPlayed(player2State, videoId);
      }
    }

    // check to see if to stop the mix
    if (stopMix[stopIndex]) {
      console.log("Stopping Mix @ " + i);
      break;
    }
  }

  playMix = false;
  if (vumeterOutput) {
    vumeterOutput.innerHTML = "";
  }
  notPlayedCount = 0;   // reset this variable
  console.log("Mix Play Done ...");
}

// function to wait a certian time before doing something
function wait(timeout) {
  return new Promise((resolve) => setTimeout(resolve, timeout));
}

// function to store if video played or not
function setVideoPlayed(playerState, videoId) {
  if (playerState != 1) {
    notPlayedCount++;

    console.log("Video Not Played: " + videoId + " / " + playerState + " Total Count: " + notPlayedCount);
    removeVideoFromQueList(videoId, true);
  }
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

  if (playMix) {
    stopMixPlay();
  }
}

// function to reload the que list
function reloadQueList() {
  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'Video Qued',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: "-1"
  }
  socket.emit('my event', jsonText);
  console.log("Reload Video Que: " + clientId);

  if (playMix) {
    stopMixPlay();
  }
}

// function to stop/pause video playback
function stopAllPlay() {
  player1.pauseVideo();
  player2.pauseVideo();
}

// function to stop a playing mix
function stopMixPlay() {
  stopMix[stopMixIndex] = true;
  stopMixIndex++;

  player1.pauseVideo();
  player2.pauseVideo();

  // hide the vumeter
  if (vumeterOutput) {
    vumeterOutput.innerHTML = "";
  }

  mixerSlider.value = 50;
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
  var loadingText = `
    <div class="modern-loader-container">
      <div class="loader-spinner"></div>
      <div class="loader-title">Loading Playlist</div>
      <div class="loader-progress-track">
        <div class="loader-progress-fill" id="loader-fill"></div>
      </div>
      <div id="loadingMessage" class="loader-message">Waiting on server...</div>
    </div>
  `;
  return loadingText;
}

// function to return the loading progress from backend 
function getLoadingProgress(playListUrl, interval) {
  loadingPlaylist = true;

  let progress = 0; // Initialize progress to 0

  const progressDisplay = document.getElementById("loadingMessage");
  progressDisplay.style.fontSize = '16px';
  progressDisplay.style.fontFamily = 'Arial, sans-serif';

  // Update progress every n seconds
  const loadingInterval = setInterval(() => {
    progress += interval; // Increment progress by a random amount (1 to 10)
    //console.log("Progress: " + progress + " Loading: " + loadingPlaylist);

    // only call backend after 4 or so seconds
    if (progress > 2) {
      jsonText = {
        data: 'Get Progress',
        url: playListUrl,
        pin: pin,
        clientId: clientId,
      }
      socket.emit('my event', jsonText);
    }

    if (!loadingPlaylist) {
      clearInterval(loadingInterval);
    }

  }, interval * 1000);
}

// update loading progress text
function updateLoadingProgress(msg) {
  let msgClientId = msg.clientId;

  if (msgClientId === clientId && loadingPlaylist) {
    let loadingText = msg.loadingText;
    const progressDisplay = document.getElementById("loadingMessage");
    if (progressDisplay) {
      progressDisplay.textContent = loadingText;
    }

    // Parse "Loading X / Y ..." to dynamically fill the progress bar
    if (loadingText && loadingText.includes("Loading")) {
      var parts = loadingText.replace("Loading", "").replace("...", "").split("/");
      if (parts.length === 2) {
        var current = parseInt(parts[0].trim());
        var total = parseInt(parts[1].trim());
        if (!isNaN(current) && !isNaN(total) && total > 0) {
          var percentage = (current / total) * 100;
          var fill = document.getElementById("loader-fill");
          if (fill) {
            fill.style.width = percentage + "%";
          }
        }
      }
    }

    if (loadingText && loadingText.includes('Error Loading')) {
      loadingPlaylist = false;
    }
  }
}

// function to merge a playlist to the default playlist
function mergePlayList() {
  username = document.getElementById("uname").value;
  var playlistUrl = document.getElementById("filter").value;

  if (playlistUrl.includes('https://www.youtube.com/playlist?list=')) {
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

// extract the youtube ID from url from ChatGPT
// https://stackoverflow.com/questions/3452546/how-do-i-get-the-youtube-video-id-from-a-url
function getYouTubeID(url) {
  if (url.length == 11) {
    return url;
  } else {
    //var regExp = /^.*((youtu.be\/)|(v\/)|(\/u\/\w\/)|(embed\/)|(watch\?))\??v?=?([^#&?]*).*/;
    //var match = url.match(regExp);
    //return (match && match[7].length == 11) ? match[7] : false;
    const patterns = [
      /(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/live\/([a-zA-Z0-9_-]{11})/,   // YouTube Live
      /(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})/,                // Short youtu.be
      /(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})/ // Standard watch URL
    ];

    for (const pattern of patterns) {
      const match = url.match(pattern);
      if (match) return match[1];
    }
  }

  return "";
}

// load a video given the videoNum and videoID
function loadVideoForPlayer(playerNum, videoId) {
  username = document.getElementById("uname").value;

  if (playerNum == 1) {
    currentVideoId1 = videoId;
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
    currentVideoId2 = videoId;
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

  // send message to server to check if the video is already saved
  jsonText = {
    data: 'Check Video Status',
    player: playerNum,
    uname: username,
    videoId: videoId
  }
  socket.emit('my event', jsonText);

  // Fetch tracklist for the loaded video in a private-safe way
  var jsonTextTrack = {
    data: 'Get TrackList Only',
    playerNum: playerNum,
    videoId: videoId,
    clientId: clientId
  };
  socket.emit('my event', jsonTextTrack);

  updateSelectedStars();
}

// add listener to filter text input to load the playlist
var filterInput = document.getElementById("filter");
filterInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    event.preventDefault(); // Prevent the default action of the Enter key
    loadPlayList();
  }
});

// function to explicity load the playlist from server
function loadPlayList() {
  username = document.getElementById("uname").value;
  var filter = document.getElementById("filter").value;
  loadPlayListForUser(username, filter);
}

// function to explicity load the playlist from server for mobile devices
function loadPlayListForMobile() {
  username = document.getElementById("uname").value;
  var filter = document.getElementById("filter").value;

  // check if the filter contains a youtube url for a video
  if (filter.includes('https://') && getYouTubeID(filter).length == 11) {
    loadVideoForMobile(1);
  } else {
    loadPlayListForUser(username, filter, false, true);
  }
}

// function to load a playlist for a user or youtube playlist linked to user
function loadPlayListForUser(username, filter, showUsername = false, mobile = false) {
  jsonText = {
    data: 'Load PlayList',
    uname: username,
    filter: filter,
    clientId: clientId,
    mobile: mobile
  }
  socket.emit('my event', jsonText);

  // if we loaded a youtube playlist clear filter input
  if (filter.startsWith("https")) {
    playListOutput.innerHTML = getLoadingText(filter);
    document.getElementById("filter").value = "";

    getLoadingProgress(filter, 2);
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
      videoId: videoId,
      clientId: clientId
    }
    socket.emit('my event', jsonText);
  } else {
    console.log('Deletion cancelled ... ' + videoId);
  }
}

// function to remove a video from the que playlist
function removeVideoFromQueList(videoId, perminent = false) {
  var pin = document.getElementById("pin").value;
  username = document.getElementById("uname").value;

  jsonText = {
    data: 'Delete Qued Video',
    player: 0,
    pin: pin,
    clientId: clientId,
    videoId: videoId,
    perminent: perminent
  }

  socket.emit('my event', jsonText);
  console.log('Delete From Que List: ' + videoId);
}

// display the video status. Currently it just states if it's saved or not
function showVideoStatus(msg) {
  var videoId = msg.videoId;
  var videoSaved = msg.videoSaved
  var playerNum = msg.player;

  if (videoSaved) {
    message = "Video ID: " + videoId + " is already in playlist.";
    if (playerNum == 1) {
      document.getElementById("videoId1Saved").innerHTML = "&#128190;";
    } else {
      document.getElementById("videoId2Saved").innerHTML = "&#128190;";
    }
  } else {
    message = "Video ID: " + videoId + " is not in playlist.";
    if (playerNum == 1) {
      document.getElementById("videoId1Saved").innerHTML = "";
    } else {
      document.getElementById("videoId2Saved").innerHTML = "";
    }
  }

  console.log("Video Status: " + message);
}

// return if a person is the DJ or not
function getIsDJ() {
  return document.getElementById("isDJ").checked;
}

// return if the person wants to be private
function isPrivate() {
  return document.getElementById("isPrivate").checked;
}

// reset data on the server
function resetValues() {
  connectedUsers = 1;
  currentVideoId1 = "";
  currentVideoId2 = "";
  updateSelectedStars();

  jsonText = {
    data: 'RESET',
    uname: username
  }
  socket.emit('my event', jsonText);

  console.log('Reseting server values ...' + uname);
}

// reload the playing html page
function reloadPlaying() {
  var pin = document.getElementById("pin").value;

  jsonText = {
    data: 'RELOAD_PLAYING',
    uname: username,
    pin: pin,
    clientId: clientId
  }
  socket.emit('my event', jsonText);
}

// add function to "smoothly" move the slider using a timer function
function moveSlideTo(playerNum) {
  var steps = 200;
  var mixStart = Number(mixerSlider.value);
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
      mixerSlider.value = mixRatio
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

  // debug message
  let message = "Mixer Ratio: " + mixRatio + " / Player 1 Volume: " + player1Vol + "% / Player 2 Volume: " + player2Vol + "%";
  mixerOutput.innerHTML = message;
  //console.log(message);

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

// function to set to mobile mode
function setMobileMode() {
  mobile = true;
}

// function to update active tracklists for marquee based on Current Video broadcast
function handleCurrentVideoTracklistUpdate(msg) {
  var playerNum = msg.player;
  var trackList = msg.trackList || null;

  if (playerNum == 1) {
    trackList1 = trackList;
    if (!trackList || trackList.length === 0) {
      var container1 = document.getElementById("track-scroller-container-1");
      if (container1) {
        if (mobile) {
          container1.style.display = "block";
          var content1 = document.getElementById("track-scroller-content-1");
          if (content1) {
            content1.innerHTML = "";
            content1.removeAttribute("data-text");
          }
        } else {
          container1.style.display = "none";
        }
      }
    }
  } else if (playerNum == 2) {
    trackList2 = trackList;
    if (!trackList || trackList.length === 0) {
      var container2 = document.getElementById("track-scroller-container-2");
      if (container2) {
        if (mobile) {
          container2.style.display = "block";
          var content2 = document.getElementById("track-scroller-content-2");
          if (content2) {
            content2.innerHTML = "";
            content2.removeAttribute("data-text");
          }
        } else {
          container2.style.display = "none";
        }
      }
    }
  }
}

// Set up interval for updating track scrollers
setInterval(updatePlayerTrackScrollers, 1000);

function updatePlayerTrackScrollers() {
  updateSinglePlayerScroller(1, player1, trackList1);
  updateSinglePlayerScroller(2, player2, trackList2);
}

function updateSinglePlayerScroller(playerNum, player, trackList) {
  var container = document.getElementById("track-scroller-container-" + playerNum);
  var content = document.getElementById("track-scroller-content-" + playerNum);

  if (!container || !content) return;

  if (trackList && trackList.length > 0) {
    var currentSeconds = 0;
    var totalSeconds = 0;

    if (player && typeof player.getCurrentTime === "function" && typeof player.getDuration === "function") {
      try {
        currentSeconds = player.getCurrentTime() || 0;
        totalSeconds = player.getDuration() || 0;
      } catch (e) {
        // Handle player state loading errors gracefully
      }
    }

    if (totalSeconds <= 0 || isNaN(totalSeconds)) {
      totalSeconds = 300; // Fallback to 5 minutes so calculations work for cued/unstarted videos
    }

    var trackDisplay = getStatelessTrackDisplay(currentSeconds, totalSeconds, trackList);

    if (content.getAttribute("data-text") !== trackDisplay) {
      content.setAttribute("data-text", trackDisplay);

      var step = totalSeconds / trackList.length;
      var index = Math.floor(currentSeconds / step);
      if (index < 0) index = 0;
      if (index >= trackList.length) index = trackList.length - 1;

      var parts = [];
      if (index > 0) {
        parts.push(`<span style="opacity: 0.65;">Prev: ${trackList[index - 1]}</span>`);
      }
      parts.push(`<span class="highlight">★ NOW PLAYING: ${trackList[index]} ★</span>`);
      if (index < trackList.length - 1) {
        parts.push(`<span style="opacity: 0.75;">Next: ${trackList[index + 1]}</span>`);
      }

      content.innerHTML = parts.join(" &nbsp; &nbsp; | &nbsp; &nbsp; ");
    }
    container.style.display = "block";
  } else {
    content.innerHTML = "";
    content.removeAttribute("data-text");
    if (mobile) {
      container.style.display = "block";
    } else {
      container.style.display = "none";
    }
  }
}

function getStatelessTrackDisplay(currentSeconds, totalSeconds, trackList) {
  if (!trackList || trackList.length === 0 || totalSeconds <= 0) return "";

  var step = totalSeconds / trackList.length;
  var index = Math.floor(currentSeconds / step);
  if (index < 0) index = 0;
  if (index >= trackList.length) index = trackList.length - 1;

  var parts = [];
  if (index > 0) {
    parts.push("Prev: " + trackList[index - 1]);
  }
  parts.push("★ NOW PLAYING: " + trackList[index] + " ★");
  if (index < trackList.length - 1) {
    parts.push("Next: " + trackList[index + 1]);
  }
  return parts.join("   |   ");
}

function handleTracklistOnlyResponse(msg) {
  var trackList = msg.trackList || null;
  var videoId = msg.videoId;

  if (videoId === currentVideoId1) {
    trackList1 = trackList;
    if (!trackList || trackList.length === 0) {
      var container1 = document.getElementById("track-scroller-container-1");
      if (container1) {
        if (mobile) {
          container1.style.display = "block";
          var content1 = document.getElementById("track-scroller-content-1");
          if (content1) {
            content1.innerHTML = "";
            content1.removeAttribute("data-text");
          }
        } else {
          container1.style.display = "none";
        }
      }
    }
  }
  if (videoId === currentVideoId2) {
    trackList2 = trackList;
    if (!trackList || trackList.length === 0) {
      var container2 = document.getElementById("track-scroller-container-2");
      if (container2) {
        if (mobile) {
          container2.style.display = "block";
          var content2 = document.getElementById("track-scroller-content-2");
          if (content2) {
            content2.innerHTML = "";
            content2.removeAttribute("data-text");
          }
        } else {
          container2.style.display = "none";
        }
      }
    }
  }
}

// update the star selection indicators in the playlist and queue tables
function updateSelectedStars() {
  var cells = document.querySelectorAll(".video-number-cell");
  cells.forEach(function (cell) {
    var videoId = cell.getAttribute("data-video-id");
    var originalIndex = cell.getAttribute("data-original-index");
    
    if (!videoId || !originalIndex) return;

    // Reset base content
    if (cell.tagName.toLowerCase() === 'span') {
      cell.innerHTML = originalIndex;
    } else {
      cell.innerHTML = "<b>" + originalIndex + ".</b>";
    }

    // Prepend appropriate star if matches player 1 or player 2 active video ID
    if (videoId === currentVideoId1) {
      cell.innerHTML = '<span class="player-star player-1-star">★</span>' + cell.innerHTML;
    } else if (videoId === currentVideoId2) {
      cell.innerHTML = '<span class="player-star player-2-star">★</span>' + cell.innerHTML;
    }
  });
}