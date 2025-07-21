#!/bin/bash
# Simple script to recursively list all mp3s along with the playtime in seconds 
# **Gemini generated code**
#!/bin/bash

if [ -z "$1" ]; then
  find . -name "*.mp3" | while IFS= read -r file; do
    bitrate=$(mp3info -p "%r" "$file") 
    duration=$(mp3info -p "%S" "$file") 
    echo "$file: $duration: $bitrate"
  done
else
  output_file="$1"
  find . -name "*.mp3" | while IFS= read -r file; do
    bitrate=$(mp3info -p "%r" "$file") 
    duration=$(mp3info -p "%S" "$file") 
    echo "$file: $duration: $bitrate" >> "$output_file"
  done
fi