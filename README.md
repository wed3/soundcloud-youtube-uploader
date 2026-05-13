# SoundCloud to YouTube Uploader

A Python command line tool that uploads a SoundCloud track to YouTube as a static cover art video.

It can fetch SoundCloud metadata, download the best available audio, render a cover art video, upload to YouTube, and set the thumbnail.

## Important

Only upload tracks you own or have explicit permission to repost.

This tool does not grant rights to upload copyrighted music.

## Disclaimer

This project was largely vibecoded and should be treated as a practical automation script rather than polished production software. Review the code before using it, especially the OAuth, upload, and file-cleanup behavior.


## Features

- Fetches SoundCloud title, uploader, artist URL, and cover art
- Downloads best available SoundCloud audio with `yt-dlp`
- Supports local artist-provided WAV/FLAC/MP3 files with `--audio`
- Renders a YouTube-ready video with `ffmpeg`
- Uploads to YouTube using OAuth
- Sets the YouTube thumbnail
- Supports public, unlisted, and private uploads
- Deletes temporary files after a successful upload
- Supports `--keep-temp` for debugging

## Requirements

System dependency:

```bash
ffmpeg
```

Python dependencies:

```bash
pip install -r requirements.txt
```

On Arch:

```bash
sudo pacman -S ffmpeg python-virtualenv
```

## Install

```bash
git clone https://github.com/wed3/soundcloud-youtube-uploader.git
cd soundcloud-youtube-uploader

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## YouTube OAuth Setup

1. Create a Google Cloud project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen.
4. Add this scope:

```text
https://www.googleapis.com/auth/youtube.upload
```

5. Create an OAuth client.
6. Choose **Desktop app**.
7. Download the OAuth JSON file.
8. Rename it:

```text
client_secret.json
```

9. Put `client_secret.json` in the project folder.

Do not commit `client_secret.json` or `token.json`.

If Google asks for authorization every week, the OAuth app is probably still in testing mode. Move it to production.

## Usage

Public upload:

```bash
python soundcloud_youtube_uploader.py "https://soundcloud.com/artist/track"
```

Unlisted upload:

```bash
python soundcloud_youtube_uploader.py "https://soundcloud.com/artist/track" --privacy unlisted
```

Private upload:

```bash
python soundcloud_youtube_uploader.py "https://soundcloud.com/artist/track" --privacy private
```

Use an artist-provided audio file:

```bash
python soundcloud_youtube_uploader.py "https://soundcloud.com/artist/track" --audio "/path/to/original.wav"
```

Keep temporary files:

```bash
python soundcloud_youtube_uploader.py "https://soundcloud.com/artist/track" --keep-temp
```

## Title Format

When run, the program asks which YouTube title format to use:

```text
1) Track title
2) Artist - Track title
3) Custom title
```

## Temporary Files

Generated files are stored in `out/` and deleted after a successful upload.

Use `--keep-temp` to preserve them for debugging.

## Bash Function

Optional convenience function for `~/.bashrc`:

```bash
soundcloud() {
  local app_dir="$HOME/soundcloud-youtube-uploader"
  local script="$app_dir/soundcloud_youtube_uploader.py"
  local venv="$app_dir/.venv/bin/activate"

  if [[ $# -lt 1 ]]; then
    echo "usage: soundcloud <soundcloud-url> [extra args]"
    return 2
  fi

  pushd "$app_dir" >/dev/null || return 1
  source "$venv"

  python "$script" "$@"

  local status=$?
  deactivate 2>/dev/null
  popd >/dev/null || true
  return "$status"
}
```

Reload:

```bash
source ~/.bashrc
```

Run:

```bash
soundcloud "https://soundcloud.com/artist/track"
```

## Cover Art

Square SoundCloud cover art is centered at full height on a black 16:9 background.

This preserves the full cover art without cropping or stretching.

## License

MIT
