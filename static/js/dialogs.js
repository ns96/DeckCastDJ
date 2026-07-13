// =====================================================================
// DIALOGS & OVERLAYS COMPONENT (dialogs.js)
// Handles creation and interaction of user interface modal dialogs.
// Declared globally for browser scope sharing with controller.js.
// =====================================================================

// helper function to parse time format HH:MM:SS or MM:SS to seconds
function parseTimeToSeconds(timeStr) {
  var parts = timeStr.split(':');
  var secs = 0;
  if (parts.length === 3) {
    secs += parseInt(parts[0]) * 3600;
    secs += parseInt(parts[1]) * 60;
    secs += parseInt(parts[2]);
  } else if (parts.length === 2) {
    secs += parseInt(parts[0]) * 60;
    secs += parseInt(parts[1]);
  }
  return secs;
}

// function to show the track list dialog to add or edit the track lists 
function addOrEditTrackList(msg) {
  var pin = document.getElementById("pin").value;
  var trackListString = "";
  var dialogTitle = msg.title;

  if (msg.trackList && msg.trackList.length !== 0) {
    trackListString = msg.trackList.join("\n");
    dialogTitle = msg.title + " [" + msg.trackList.length + " Tracks]";
  }

  // 1. Remove existing dialogs to avoid visual overlaps
  var oldModal = document.getElementById("app-dialog-overlay");
  if (oldModal) oldModal.remove();

  // 2. Build Dialog Overlay and Structure
  var overlay = document.createElement("div");
  overlay.id = "app-dialog-overlay";
  overlay.className = "app-modal-overlay";

  var content = document.createElement("div");
  content.className = "app-modal-content";

  // Modal Header
  var header = document.createElement("div");
  header.className = "app-modal-header";
  header.innerHTML = `<h3>Track List: ${dialogTitle}</h3><button class="app-modal-close-btn">&times;</button>`;
  content.appendChild(header);

  // Close binder
  header.querySelector(".app-modal-close-btn").onclick = function () { overlay.remove(); };

  // Modal Body containing the textarea
  var body = document.createElement("div");
  body.className = "app-modal-body";

  var textarea = document.createElement("textarea");
  textarea.className = "tl-textarea";
  textarea.placeholder = "Enter tracklist with line breaks, e.g.\n00:00 Intro\n01:30 Main Beat\n03:15 Outro";
  textarea.value = trackListString;
  body.appendChild(textarea);
  content.appendChild(body);

  // Modal Footer containing Save & Close / Cancel buttons
  var footer = document.createElement("div");
  footer.className = "app-modal-footer";

  var cancelBtn = document.createElement("button");
  cancelBtn.className = "secondary-btn";
  cancelBtn.innerText = "Cancel";
  cancelBtn.onclick = function () { overlay.remove(); };

  var saveBtn = document.createElement("button");
  saveBtn.innerText = "Save & Close";
  saveBtn.onclick = function () {
    var newTracklist = textarea.value;

    jsonText = {
      data: 'Add TrackList',
      pin: pin,
      videoId: msg.videoId,
      tracklist: newTracklist
    };
    socket.emit('my event', jsonText);
    console.log("Sent Add TrackList to backend:", jsonText);
    overlay.remove();
  };

  footer.appendChild(cancelBtn);
  footer.appendChild(saveBtn);
  content.appendChild(footer);

  overlay.appendChild(content);
  document.body.appendChild(overlay);

  // Close modal when clicking on backdrop
  overlay.onclick = function (e) {
    if (e.target === overlay) {
      overlay.remove();
    }
  };
}

// function called by the playlist track rows to load and show the tracklist dialog
function showTrackListDialog(videoId, title) {
  getTrackList(videoId, title);
}

// functon to kick of the process to add or edit a tracklist for a particular video
function showTrackListDialogForPlayer(playerNum) {
  var videoId;
  var title;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
    title = player1.getVideoData()['title'];
  } else {
    videoId = player2.getVideoData()['video_id'];
    title = player2.getVideoData()['title'];
  }

  getTrackList(videoId, title);
}

// function to get tracklist and eventially display the track list dialog
function getTrackList(videoId, title) {
  var jsonText = {
    data: 'Edit TrackList',
    videoId: videoId,
    title: title,
    clientId: clientId
  }
  socket.emit('my event', jsonText);

  console.log("Getting Track List For Video ID: " + videoId + "\n" + title);
}

// function to show the bookmark list dialog to select or delete the bookmarks 
// for a particular video 
function selectOrEditBookmarks(msg) {
  var pin = document.getElementById("pin").value;
  var dialogTitle = msg.title || "Bookmarks";
  var videoId = msg.videoId;
  var playerNum = msg.playerNum;

  // Disable jump link if noJump is true or active playerNum is not provided
  var noJump = msg.noJump || !msg.playerNum;

  var bookmarks = [];
  if (msg.bookmarks && Array.isArray(msg.bookmarks)) {
    bookmarks = msg.bookmarks.map(function (item) {
      return { time: parseFloat(item[0]), desc: item[1] || "" };
    });
  }

  bookmarks.sort(function (a, b) { return a.time - b.time; });

  var oldModal = document.getElementById("app-dialog-overlay");
  if (oldModal) oldModal.remove();

  var overlay = document.createElement("div");
  overlay.id = "app-dialog-overlay";
  overlay.className = "app-modal-overlay";

  var content = document.createElement("div");
  content.className = "app-modal-content";

  // Modal Header
  var header = document.createElement("div");
  header.className = "app-modal-header";
  header.innerHTML = `<h3>Bookmarks: ${dialogTitle}</h3><button class="app-modal-close-btn">&times;</button>`;
  content.appendChild(header);

  header.querySelector(".app-modal-close-btn").onclick = function () { overlay.remove(); };

  // Modal Body containing bookmarks table
  var body = document.createElement("div");
  body.className = "app-modal-body";

  if (bookmarks.length === 0) {
    body.innerHTML = "<p style='text-align:center; opacity: 0.7;'>No bookmarks loaded for this video.</p>";
  } else {
    var table = document.createElement("table");
    table.className = "bm-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th width="20%">Time</th>
          <th width="50%">Description</th>
          <th width="30%">Actions</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    var tbody = table.querySelector("tbody");

    bookmarks.forEach(function (bm) {
      var row = document.createElement("tr");
      var timeStr = toHHMMSS(Math.round(bm.time));

      if (noJump) {
        row.innerHTML = `
          <td><span style="font-weight: bold; color: var(--text-main);">${timeStr}</span></td>
          <td><input type="text" class="bm-input-desc" value="${bm.desc}" /></td>
          <td>
            <button class="bm-action-btn bm-save-btn">Save</button>
            <button class="bm-action-btn bm-delete-btn danger-btn">Del</button>
          </td>
        `;
      } else {
        row.innerHTML = `
          <td><span class="bm-time-link" title="Jump to time">${timeStr}</span></td>
          <td><input type="text" class="bm-input-desc" value="${bm.desc}" /></td>
          <td>
            <button class="bm-action-btn bm-save-btn">Save</button>
            <button class="bm-action-btn bm-delete-btn danger-btn">Del</button>
          </td>
        `;

        // Jump Action
        row.querySelector(".bm-time-link").onclick = function () {
          var player = (playerNum == 1) ? player1 : player2;
          if (player && typeof player.seekTo === "function") {
            player.seekTo(bm.time, true);
          }
        };
      }

      // Save/Edit action
      row.querySelector(".bm-save-btn").onclick = function () {
        var newDesc = row.querySelector(".bm-input-desc").value;
        jsonText = {
          data: 'Edit Bookmark',
          pin: pin,
          clientId: clientId,
          videoId: videoId,
          time: bm.time,
          desc: newDesc
        };
        socket.emit('my event', jsonText);
        console.log(`Edited Bookmark: ${bm.time}s -> ${newDesc}`);
        alert("Bookmark saved!");
      };

      // Delete Action
      row.querySelector(".bm-delete-btn").onclick = function () {
        if (confirm(`Are you sure you want to delete bookmark at ${timeStr}?`)) {
          jsonText = {
            data: 'Delete Bookmark',
            pin: pin,
            clientId: clientId,
            videoId: videoId,
            time: bm.time
          };
          socket.emit('my event', jsonText);
          row.remove();
          if (tbody.children.length === 0) {
            body.innerHTML = "<p style='text-align:center; opacity: 0.7;'>No bookmarks loaded for this video.</p>";
          }
        }
      };

      tbody.appendChild(row);
    });

    body.appendChild(table);
  }
  content.appendChild(body);

  // Modal Footer containing "Remove All" and standard Close buttons
  var footer = document.createElement("div");
  footer.className = "app-modal-footer";
  footer.style.justifyContent = "space-between"; // Push Remove All to the left

  var removeAllBtn = document.createElement("button");
  removeAllBtn.className = "danger-btn";
  removeAllBtn.innerText = "Remove All";

  if (bookmarks.length === 0) {
    removeAllBtn.disabled = true;
    removeAllBtn.style.opacity = 0.5;
  }

  removeAllBtn.onclick = function () {
    if (confirm("WARNING: Are you sure you want to permanently delete ALL bookmarks for this video?")) {
      jsonText = {
        data: 'Clear Bookmarks',
        pin: pin,
        clientId: clientId,
        videoId: videoId
      };
      socket.emit('my event', jsonText);
      overlay.remove();
      alert("All bookmarks removed!");
    }
  };

  var closeBtn = document.createElement("button");
  closeBtn.className = "secondary-btn";
  closeBtn.innerText = "Close";
  closeBtn.onclick = function () { overlay.remove(); };

  footer.appendChild(removeAllBtn);
  footer.appendChild(closeBtn);
  content.appendChild(footer);

  overlay.appendChild(content);
  document.body.appendChild(overlay);

  // Close modal when clicking outside content area
  overlay.onclick = function (e) {
    if (e.target === overlay) {
      overlay.remove();
    }
  };
}

// function to show the track numbers dialog for a video mix
function showTrackNumbersDialog(msg) {
  // Configurable Extraction Defaults (only initialized once per page load)
  if (!window.activeTNSettings) {
    window.activeTNSettings = {
      method: "adaptive",
      min_distance: 45,
      adaptive_window: 150,
      offset: 0.04,
      prominence: 0.035
    };
  }

  var dialogTitle = msg.title || "Track Numbers";
  var videoId = msg.videoId;
  var playerNum = msg.playerNum;

  var tracks = msg.trackNumbers || [];

  // Remove any existing overlay
  var oldModal = document.getElementById("app-dialog-overlay");
  if (oldModal) oldModal.remove();

  var overlay = document.createElement("div");
  overlay.id = "app-dialog-overlay";
  overlay.className = "app-modal-overlay";

  var content = document.createElement("div");
  content.className = "app-modal-content";
  content.style.maxWidth = "600px";

  // Modal Header
  var header = document.createElement("div");
  header.className = "app-modal-header";
  header.innerHTML = `<h3>Track Numbers: ${dialogTitle}</h3><button class="app-modal-close-btn">&times;</button>`;
  content.appendChild(header);

  header.querySelector(".app-modal-close-btn").onclick = function () { overlay.remove(); };

  // Modal Body containing settings form and tracks list
  var body = document.createElement("div");
  body.className = "app-modal-body";

  // Create tuning inputs HTML
  var settingsDiv = document.createElement("div");
  settingsDiv.className = "tn-settings-container";
  settingsDiv.style.marginBottom = "20px";
  settingsDiv.style.padding = "15px";
  settingsDiv.style.border = "1px solid var(--border-color)";
  settingsDiv.style.borderRadius = "8px";
  settingsDiv.style.backgroundColor = "var(--bg-control)";

  settingsDiv.innerHTML = `
    <h4 style="margin: 0 0 12px 0; color: var(--text-header); font-size: 14px;">Tuning Settings</h4>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
      <div>
        <label for="tn-method" style="display:block; font-size:11px; font-weight:bold; margin-bottom:4px;">Method:</label>
        <select id="tn-method" style="width:100%; box-sizing:border-box;">
          <option value="adaptive" ${window.activeTNSettings.method === "adaptive" ? "selected" : ""}>Adaptive Threshold</option>
          <option value="fixed" ${window.activeTNSettings.method === "fixed" ? "selected" : ""}>Fixed Prominence</option>
        </select>
      </div>
      <div>
        <label for="tn-min-distance" style="display:block; font-size:11px; font-weight:bold; margin-bottom:4px;">Min Distance (s):</label>
        <input type="number" id="tn-min-distance" value="${window.activeTNSettings.min_distance}" min="5" max="600" style="width:100%; box-sizing:border-box; padding:6px; font-size:12px;" />
      </div>
    </div>
    
    <div id="tn-adaptive-settings" style="display: ${window.activeTNSettings.method === "adaptive" ? "grid" : "none"}; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
      <div>
        <label for="tn-adaptive-window" style="display:block; font-size:11px; font-weight:bold; margin-bottom:4px;">Adaptive Window (s):</label>
        <input type="number" id="tn-adaptive-window" value="${window.activeTNSettings.adaptive_window}" min="10" max="600" style="width:100%; box-sizing:border-box; padding:6px; font-size:12px;" />
      </div>
      <div>
        <label for="tn-offset" style="display:block; font-size:11px; font-weight:bold; margin-bottom:4px;">Threshold Offset:</label>
        <input type="number" id="tn-offset" value="${window.activeTNSettings.offset}" step="0.001" min="0.001" max="0.5" style="width:100%; box-sizing:border-box; padding:6px; font-size:12px;" />
      </div>
    </div>
    
    <div id="tn-fixed-settings" style="display: ${window.activeTNSettings.method === "fixed" ? "block" : "none"}; margin-bottom: 12px;">
      <label for="tn-prominence" style="display:block; font-size:11px; font-weight:bold; margin-bottom:4px;">Peak Prominence:</label>
      <input type="number" id="tn-prominence" value="${window.activeTNSettings.prominence}" step="0.001" min="0.001" max="0.5" style="width:100%; box-sizing:border-box; padding:6px; font-size:12px;" />
    </div>
    
    <div style="margin-top: 15px;">
      <button id="tn-extract-btn" style="width: 100%; padding: 8px; font-size: 13px;">Extract Tracks</button>
    </div>
    <div id="tn-status" style="margin-top: 10px; font-size: 12px; text-align: center; color: var(--accent-primary); display: none;">
      <span class="loader-spinner" style="width: 14px; height: 14px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 8px;"></span>
      <span id="tn-status-text" style="vertical-align: middle;">Extracting tracks (SciPy analysis takes ~10-30s)...</span>
    </div>
  `;
  body.appendChild(settingsDiv);

  // Toggle settings visibility based on method selection
  var methodSelect = settingsDiv.querySelector("#tn-method");
  var adaptiveSettings = settingsDiv.querySelector("#tn-adaptive-settings");
  var fixedSettings = settingsDiv.querySelector("#tn-fixed-settings");

  methodSelect.onchange = function () {
    if (methodSelect.value === "adaptive") {
      adaptiveSettings.style.display = "grid";
      fixedSettings.style.display = "none";
    } else {
      adaptiveSettings.style.display = "none";
      fixedSettings.style.display = "block";
    }
  };

  // Setup extraction click action
  var extractBtn = settingsDiv.querySelector("#tn-extract-btn");
  var statusDiv = settingsDiv.querySelector("#tn-status");

  extractBtn.onclick = function () {
    extractBtn.disabled = true;
    statusDiv.style.display = "block";
    methodSelect.disabled = true;
    settingsDiv.querySelectorAll("input").forEach(function (input) { input.disabled = true; });

    // Update active settings so they are preserved upon reload
    window.activeTNSettings.method = methodSelect.value;
    window.activeTNSettings.min_distance = parseInt(settingsDiv.querySelector("#tn-min-distance").value) || window.activeTNSettings.min_distance;
    window.activeTNSettings.prominence = parseFloat(settingsDiv.querySelector("#tn-prominence").value) || window.activeTNSettings.prominence;
    window.activeTNSettings.adaptive_window = parseInt(settingsDiv.querySelector("#tn-adaptive-window").value) || window.activeTNSettings.adaptive_window;
    window.activeTNSettings.offset = parseFloat(settingsDiv.querySelector("#tn-offset").value) || window.activeTNSettings.offset;

    var jsonText = {
      data: 'Extract Track Numbers',
      videoId: videoId,
      playerNum: playerNum,
      title: dialogTitle,
      clientId: clientId,
      method: window.activeTNSettings.method,
      min_distance: window.activeTNSettings.min_distance,
      prominence: window.activeTNSettings.prominence,
      adaptive_window: window.activeTNSettings.adaptive_window,
      offset: window.activeTNSettings.offset
    };
    socket.emit('my event', jsonText);
  };

  // Create list header early so helper/buttons can refer to it
  var listHeader = document.createElement("h4");
  listHeader.style.margin = "20px 0 10px 0";
  listHeader.style.borderBottom = "1px solid var(--border-color)";
  listHeader.style.paddingBottom = "5px";
  listHeader.style.fontSize = "14px";

  // Helper function to update the playing track status in the list header
  function updatePlayingTrackHeader(idx) {
    var activeIdx = (idx !== undefined) ? idx : getCurrentTrackIndex();
    var trackText = "Current Tracks (" + tracks.length + ")";
    if (activeIdx !== -1 && activeIdx < tracks.length) {
      var trackLine = tracks[activeIdx];
      var spaceIdx = trackLine.indexOf(' ');
      if (spaceIdx !== -1) {
        var timeStr = trackLine.substring(0, spaceIdx);
        var label = trackLine.substring(spaceIdx + 1);
        trackText += " || Playing: " + label + " (" + timeStr + ")";
      }
    }
    listHeader.innerText = trackText;
  }

  // Helper function to find the current active track index
  function getCurrentTrackIndex() {
    var player = (playerNum == 1) ? player1 : player2;
    if (!player || typeof player.getCurrentTime !== "function") return -1;
    var currentTime = player.getCurrentTime();
    var activeIdx = -1;
    for (var i = 0; i < tracks.length; i++) {
      var trackLine = tracks[i];
      var spaceIdx = trackLine.indexOf(' ');
      if (spaceIdx === -1) continue;
      var timeStr = trackLine.substring(0, spaceIdx);
      var secs = parseTimeToSeconds(timeStr);
      if (currentTime >= secs) {
        activeIdx = i;
      } else {
        break; // tracks are sorted chronologically
      }
    }
    return activeIdx;
  }

  // Playback controls row UI container and buttons
  var controlsRow = document.createElement("div");
  controlsRow.className = "tn-controls-row";
  controlsRow.style.display = "flex";
  controlsRow.style.justifyContent = "center";
  controlsRow.style.gap = "15px";
  controlsRow.style.margin = "10px 0 15px 0";

  var prevBtn = document.createElement("button");
  prevBtn.className = "secondary-btn";
  prevBtn.innerHTML = "&#9198; PREV"; // Previous track symbol (⏮)
  prevBtn.style.padding = "6px 12px";
  prevBtn.style.fontSize = "12px";

  var playBtn = document.createElement("button");
  playBtn.innerHTML = "&#9654; PLAY"; // Play symbol (▶)
  playBtn.style.padding = "6px 16px";
  playBtn.style.fontSize = "12px";

  var nextBtn = document.createElement("button");
  nextBtn.className = "secondary-btn";
  nextBtn.innerHTML = "NEXT &#9197;"; // Next track symbol (⏭)
  nextBtn.style.padding = "6px 12px";
  nextBtn.style.fontSize = "12px";

  var addBmBtn = document.createElement("button");
  addBmBtn.className = "secondary-btn";
  addBmBtn.innerHTML = "Add BM";
  addBmBtn.style.padding = "6px 12px";
  addBmBtn.style.fontSize = "12px";

  if (tracks.length === 0) {
    prevBtn.disabled = true;
    prevBtn.style.opacity = 0.5;
    nextBtn.disabled = true;
    nextBtn.style.opacity = 0.5;
  }

  // Playback click handlers
  playBtn.onclick = function () {
    var player = (playerNum == 1) ? player1 : player2;
    if (player && typeof player.playVideo === "function") {
      if (typeof player.getPlayerState === "function" && player.getPlayerState() !== 1) {
        player.playVideo();
      }
    }
  };

  nextBtn.onclick = function () {
    var player = (playerNum == 1) ? player1 : player2;
    if (!player || typeof player.seekTo !== "function") return;
    var currentIdx = getCurrentTrackIndex();
    var targetIdx = (currentIdx === -1) ? 0 : currentIdx + 1;
    if (targetIdx < tracks.length) {
      var trackLine = tracks[targetIdx];
      var spaceIdx = trackLine.indexOf(' ');
      if (spaceIdx !== -1) {
        var timeStr = trackLine.substring(0, spaceIdx);
        var secs = parseTimeToSeconds(timeStr);
        player.seekTo(secs, true);
        updatePlayingTrackHeader(targetIdx);
      }
    }
  };

  prevBtn.onclick = function () {
    var player = (playerNum == 1) ? player1 : player2;
    if (!player || typeof player.seekTo !== "function") return;
    var currentIdx = getCurrentTrackIndex();
    if (currentIdx === -1 || currentIdx === 0) {
      player.seekTo(0, true);
      updatePlayingTrackHeader(0);
      return;
    }
    var prevIdx = currentIdx - 1;
    var trackLine = tracks[prevIdx];
    var spaceIdx = trackLine.indexOf(' ');
    if (spaceIdx !== -1) {
      var timeStr = trackLine.substring(0, spaceIdx);
      var secs = parseTimeToSeconds(timeStr);
      player.seekTo(secs, true);
      updatePlayingTrackHeader(prevIdx);
    }
  };

  // Bookmark click handler to add active track bookmark silently
  addBmBtn.onclick = function () {
    var player = (playerNum == 1) ? player1 : player2;
    if (!player || typeof player.getCurrentTime !== "function") return;

    var currentIdx = getCurrentTrackIndex();
    var time;
    var desc;
    var pin = document.getElementById("pin").value;
    var username = document.getElementById("uname").value;

    if (currentIdx !== -1 && currentIdx < tracks.length) {
      var trackLine = tracks[currentIdx];
      var spaceIdx = trackLine.indexOf(' ');
      if (spaceIdx !== -1) {
        var timeStr = trackLine.substring(0, spaceIdx);
        time = parseTimeToSeconds(timeStr);
        desc = trackLine.substring(spaceIdx + 1); // e.g. "Track 2"
      } else {
        time = player.getCurrentTime();
        desc = "Bookmark by " + username;
      }
    } else {
      time = player.getCurrentTime();
      desc = "Bookmark by " + username;
    }

    var jsonText = {
      data: 'Add Bookmark',
      pin: pin,
      clientId: clientId,
      videoId: videoId,
      uname: username,
      time: time,
      desc: desc,
      silent: true
    };
    socket.emit('my event', jsonText);

    // Visual feedback
    var originalText = addBmBtn.innerHTML;
    addBmBtn.innerHTML = "Added!";
    addBmBtn.style.backgroundColor = "#28a745";
    addBmBtn.disabled = true;
    setTimeout(function () {
      addBmBtn.innerHTML = originalText;
      addBmBtn.style.backgroundColor = "";
      addBmBtn.disabled = false;
    }, 1500);
  };

  controlsRow.appendChild(prevBtn);
  controlsRow.appendChild(playBtn);
  controlsRow.appendChild(nextBtn);
  controlsRow.appendChild(addBmBtn);
  body.appendChild(controlsRow);

  // Initialize and append list header
  updatePlayingTrackHeader();
  body.appendChild(listHeader);

  // Tracks list/table
  var tracksContainer = document.createElement("div");
  tracksContainer.id = "tn-tracks-container";

  if (tracks.length === 0) {
    tracksContainer.innerHTML = "<p style='text-align:center; opacity: 0.7; font-size:12px; margin: 15px 0;'>No track numbers extracted yet for this video.</p>";
  } else {
    var table = document.createElement("table");
    table.className = "bm-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th width="30%">Time</th>
          <th width="70%">Track Label</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    var tbody = table.querySelector("tbody");

    tracks.forEach(function (trackLine) {
      // Split into time and title
      // Format is e.g. "03:46 Track 2" or "01:02:31 Track 15"
      var spaceIdx = trackLine.indexOf(' ');
      if (spaceIdx === -1) return;
      var timeStr = trackLine.substring(0, spaceIdx);
      var label = trackLine.substring(spaceIdx + 1);

      var row = document.createElement("tr");
      row.innerHTML = `
        <td><span class="bm-time-link" title="Jump to time">${timeStr}</span></td>
        <td><span style="color: var(--text-main); font-weight: bold;">${label}</span></td>
      `;

      var secs = parseTimeToSeconds(timeStr);
      row.querySelector(".bm-time-link").onclick = function () {
        var player = (playerNum == 1) ? player1 : player2;
        if (player && typeof player.seekTo === "function") {
          player.seekTo(secs, true);
          var idx = tracks.indexOf(trackLine);
          updatePlayingTrackHeader(idx);
        }
      };

      tbody.appendChild(row);
    });

    tracksContainer.appendChild(table);
  }
  body.appendChild(tracksContainer);
  content.appendChild(body);

  // Modal Footer
  var footer = document.createElement("div");
  footer.className = "app-modal-footer";
  footer.style.justifyContent = "space-between";

  var deleteBtn = document.createElement("button");
  deleteBtn.className = "danger-btn";
  deleteBtn.innerText = "Delete Tracks";

  if (tracks.length === 0) {
    deleteBtn.disabled = true;
    deleteBtn.style.opacity = 0.5;
  }

  deleteBtn.onclick = function () {
    if (confirm("WARNING: Are you sure you want to permanently delete all extracted track numbers for this video?")) {
      var jsonText = {
        data: 'Delete Track Numbers',
        videoId: videoId,
        playerNum: playerNum,
        title: dialogTitle,
        clientId: clientId
      };
      socket.emit('my event', jsonText);
    }
  };

  var closeBtn = document.createElement("button");
  closeBtn.className = "secondary-btn";
  closeBtn.innerText = "Close";
  closeBtn.onclick = function () { overlay.remove(); };

  footer.appendChild(deleteBtn);
  footer.appendChild(closeBtn);
  content.appendChild(footer);

  overlay.appendChild(content);
  document.body.appendChild(overlay);

  // Close modal when clicking outside content area
  overlay.onclick = function (e) {
    if (e.target === overlay) {
      overlay.remove();
    }
  };
}

// function to view the bookmark for the current video
function viewBookmarksForPlayer(playerNum) {
  var videoId;
  var title;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
    title = player1.getVideoData()['title'];
  } else {
    videoId = player2.getVideoData()['video_id'];
    title = player2.getVideoData()['title'];
  }

  getBookmarks(playerNum, videoId, title);
}

// function to actual display the bookmarks dialog for a particular video
function getBookmarks(playerNum, videoId, title) {
  var jsonText = {
    data: 'View Bookmarks',
    playerNum: playerNum,
    videoId: videoId,
    title: title,
    clientId: clientId
  }
  socket.emit('my event', jsonText);

  console.log("Getting Bookmarks For Video ID: " + videoId + "\n" + title);
}

// function to request track numbers view for the active video of a player
function showTrackNumbersDialogForPlayer(playerNum) {
  var videoId;
  var title;

  if (playerNum == 1) {
    videoId = player1.getVideoData()['video_id'];
    title = player1.getVideoData()['title'];
  } else {
    videoId = player2.getVideoData()['video_id'];
    title = player2.getVideoData()['title'];
  }

  getTrackNumbers(playerNum, videoId, title);
}

// function to request track numbers list from the server
function getTrackNumbers(playerNum, videoId, title) {
  var jsonText = {
    data: 'View Track Numbers',
    playerNum: playerNum,
    videoId: videoId,
    title: title,
    clientId: clientId
  };
  socket.emit('my event', jsonText);

  console.log("Getting Track Numbers For Video ID: " + videoId + "\n" + title);
}

// function to display the bookmarks dialog without jump links for a search/playlist row
function showBookmarksDialogNoLinks(videoId, title) {
  var pin = document.getElementById("pin").value;
  var jsonText = {
    data: 'View Bookmarks',
    pin: pin,
    clientId: clientId,
    videoId: videoId,
    title: title,
    noJump: true
  }
  socket.emit('my event', jsonText);

  console.log("Getting Bookmarks (No Links) For Video ID: " + videoId + "\n" + title);
}
