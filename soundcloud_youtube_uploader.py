#!/usr/bin/env python3

"""
soundcloud_to_youtube.py

Deps inside venv:

  python -m pip install requests pillow yt-dlp google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

System dep:

  sudo pacman -S ffmpeg

Run:

  python soundcloud_to_youtube.py "https://soundcloud.com/artist/track" --privacy unlisted

Better quality, with artist-provided source audio:

  python soundcloud_to_youtube.py "https://soundcloud.com/artist/track" \
    --audio "/path/to/original.wav" \
    --privacy unlisted

Keep generated files for debugging:

  python soundcloud_to_youtube.py "https://soundcloud.com/artist/track" \
    --privacy unlisted \
    --keep-temp
"""

import argparse
import pathlib
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image, ImageOps
from yt_dlp import YoutubeDL

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text).strip("_")
    return text[:120] or "soundcloud_upload"


def infer_artist_url(soundcloud_url: str) -> str:
    parsed = urlparse(soundcloud_url)
    parts = [p for p in parsed.path.split("/") if p]

    if not parts:
        return soundcloud_url

    return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"


def fetch_soundcloud_metadata(soundcloud_url: str) -> dict:
    response = requests.get(
        "https://soundcloud.com/oembed",
        params={"format": "json", "url": soundcloud_url},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def clean_title(raw_title: str, artist_name: str | None) -> str:
    title = raw_title.strip()

    if artist_name:
        suffix = f" by {artist_name}"
        if title.lower().endswith(suffix.lower()):
            title = title[: -len(suffix)].strip()

    return title[:100] or "untitled soundcloud track"


def choose_youtube_title(track_title: str, artist_name: str) -> str:
    artist_name = artist_name.strip() or "unknown artist"

    option_1 = track_title
    option_2 = f"{artist_name} - {track_title}"

    print()
    print("Choose YouTube title format:")
    print(f"  1) {option_1}")
    print(f"  2) {option_2}")
    print("  3) Custom title")
    print()

    while True:
        choice = input("Pick 1, 2, or 3 [1]: ").strip() or "1"

        if choice == "1":
            return option_1[:100]

        if choice == "2":
            return option_2[:100]

        if choice == "3":
            custom = input("Custom title: ").strip()
            if custom:
                return custom[:100]
            print("Title cannot be empty.")
            continue

        print("Invalid choice. Pick 1, 2, or 3.")


def best_guess_cover_url(thumbnail_url: str) -> str:
    candidates = []

    replacements = [
        ("-large.", "-original."),
        ("-large.", "-t500x500."),
        ("-large.", "-crop."),
        ("-t500x500.", "-original."),
        ("-crop.", "-original."),
    ]

    for old, new in replacements:
        if old in thumbnail_url:
            candidates.append(thumbnail_url.replace(old, new))

    candidates.append(thumbnail_url)

    seen = set()
    deduped = []

    for url in candidates:
        if url not in seen:
            seen.add(url)
            deduped.append(url)

    for url in deduped:
        try:
            response = requests.head(url, timeout=15, allow_redirects=True)
            if response.status_code == 200:
                return url
        except requests.RequestException:
            pass

    return thumbnail_url


def download_file(url: str, path: pathlib.Path) -> None:
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()

        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    file.write(chunk)


def make_cover_image(
    cover_path: pathlib.Path,
    output_path: pathlib.Path,
    size: tuple[int, int],
    max_bytes: int | None = None,
) -> None:
    """
    Preserves the entire cover art.

    For square cover art:
      1920x1080 video frame -> 1080x1080 cover centered on black
      1280x720 thumbnail    -> 720x720 cover centered on black

    No cropping. No stretching. Side bars are expected for square art.
    """
    source = Image.open(cover_path).convert("RGB")
    background = Image.new("RGB", size, (0, 0, 0))

    cover = ImageOps.contain(
        source,
        size,
        method=Image.Resampling.LANCZOS,
    )

    x = (size[0] - cover.width) // 2
    y = (size[1] - cover.height) // 2
    background.paste(cover, (x, y))

    quality = 95

    while True:
        background.save(output_path, "JPEG", quality=quality, optimize=True)

        if max_bytes is None:
            return

        if output_path.stat().st_size <= max_bytes:
            return

        quality -= 5

        if quality < 70:
            return


def download_soundcloud_audio(soundcloud_url: str, outdir: pathlib.Path, stem: str) -> pathlib.Path:
    before = set(outdir.iterdir())
    output_template = str(outdir / f"{stem}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": False,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(soundcloud_url, download=True)

    requested_downloads = info.get("requested_downloads") or []

    for item in requested_downloads:
        filepath = item.get("filepath")
        if filepath:
            path = pathlib.Path(filepath)
            if path.exists():
                return path

    after = set(outdir.iterdir())
    new_files = [
        path
        for path in after - before
        if path.is_file()
        and not path.name.endswith(".part")
        and path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
    ]

    if new_files:
        return sorted(new_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    fallback_files = [
        path
        for path in outdir.glob(f"{stem}.*")
        if path.is_file()
        and path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
    ]

    if fallback_files:
        return sorted(fallback_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    raise RuntimeError("Could not find downloaded SoundCloud audio file.")


def render_video(audio_path: pathlib.Path, image_path: pathlib.Path, output_path: pathlib.Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "384k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    subprocess.run(command, check=True)


def get_youtube_client_manual_oauth(
    client_secret_path: pathlib.Path,
    token_path: pathlib.Path,
):
    credentials = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_path),
            SCOPES,
            redirect_uri="http://127.0.0.1:8080/",
        )

        auth_url, expected_state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )

        print()
        print("Open Google authorization URL in browser:")
        print()
        print(auth_url)
        print()
        print("After approval, Google should redirect to a URL beginning with:")
        print()
        print("  http://127.0.0.1:8080/?")
        print()
        print("The page may look broken or refuse to load. That is fine.")
        print("Copy the full final URL from the browser address bar and paste it below.")
        print()

        redirect_response = input("Paste final redirected URL here: ").strip()

        if not redirect_response.startswith("http://127.0.0.1:8080/"):
            raise RuntimeError(
                "Expected final redirected URL beginning with "
                "http://127.0.0.1:8080/. Something else was pasted."
            )

        parsed_redirect = urlparse(redirect_response)
        query = parse_qs(parsed_redirect.query)

        if "error" in query:
            raise RuntimeError(f"Google OAuth error: {query['error'][0]}")

        state_values = query.get("state")
        if not state_values:
            raise RuntimeError("Could not find state=... in redirected URL.")

        if state_values[0] != expected_state:
            raise RuntimeError("OAuth state mismatch. Rerun and try again.")

        code_values = query.get("code")
        if not code_values:
            raise RuntimeError("Could not find code=... in redirected URL.")

        authorization_code = code_values[0]

        flow.fetch_token(code=authorization_code)
        credentials = flow.credentials

    token_path.write_text(credentials.to_json())

    return build("youtube", "v3", credentials=credentials)


def upload_video(
    youtube,
    video_path: pathlib.Path,
    title: str,
    description: str,
    privacy: str,
) -> str:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "10",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=1024 * 1024 * 8,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    return response["id"]


def set_thumbnail(youtube, video_id: str, thumbnail_path: pathlib.Path) -> None:
    media = MediaFileUpload(str(thumbnail_path))

    youtube.thumbnails().set(
        videoId=video_id,
        media_body=media,
    ).execute()


def build_description(title: str, soundcloud_url: str, artist_url: str) -> str:
    return (
        f"{title}\n\n"
        f"Artist on SoundCloud: {artist_url}\n"
        f"Original SoundCloud track: {soundcloud_url}\n\n"
    )


def cleanup_temp_files(paths: list[pathlib.Path]) -> None:
    print("Cleaning up temporary files.")

    for path in paths:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                print(f"Deleted: {path}")
        except Exception as error:
            print(f"Warning: could not delete {path}: {error}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a permitted SoundCloud track to YouTube with cover-art video and thumbnail."
    )

    parser.add_argument("soundcloud_url")

    parser.add_argument(
        "--audio",
        help=(
            "Optional local artist-provided audio file. "
            "If omitted, best available SoundCloud audio is downloaded."
        ),
    )

    parser.add_argument(
        "--privacy",
        default="public",
        choices=["private", "unlisted", "public"],
    )

    parser.add_argument(
        "--client-secret",
        default="client_secret.json",
        help="Google OAuth client secret JSON.",
    )

    parser.add_argument(
        "--token",
        default="token.json",
        help="Saved OAuth token path.",
    )

    parser.add_argument(
        "--outdir",
        default="out",
        help="Folder for downloaded audio, cover art, rendered video, and thumbnail.",
    )

    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep generated files in the out folder after upload.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    client_secret_path = pathlib.Path(args.client_secret).expanduser().resolve()

    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"Missing OAuth client secret file: {client_secret_path}"
        )

    outdir = pathlib.Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    print("Fetching SoundCloud metadata.")

    metadata = fetch_soundcloud_metadata(args.soundcloud_url)

    raw_title = metadata.get("title", "").strip()
    artist_name = metadata.get("author_name") or ""
    artist_url = metadata.get("author_url") or infer_artist_url(args.soundcloud_url)
    thumbnail_url = metadata.get("thumbnail_url")

    if not thumbnail_url:
        raise RuntimeError("SoundCloud did not return cover art.")

    track_title = clean_title(raw_title, artist_name)
    youtube_title = choose_youtube_title(track_title, artist_name)

    stem = slugify(youtube_title)

    print()
    print(f"Track title: {track_title}")
    print(f"Uploader: {artist_name or 'unknown'}")
    print(f"YouTube title: {youtube_title}")
    print(f"Artist URL: {artist_url}")
    print()

    print("Authenticating YouTube account.")

    youtube = get_youtube_client_manual_oauth(
        client_secret_path=client_secret_path,
        token_path=pathlib.Path(args.token).expanduser().resolve(),
    )

    cover_url = best_guess_cover_url(thumbnail_url)

    raw_cover_path = outdir / f"{stem}_cover_raw.jpg"
    video_frame_path = outdir / f"{stem}_video_frame.jpg"
    youtube_thumbnail_path = outdir / f"{stem}_youtube_thumbnail.jpg"
    video_path = outdir / f"{stem}.mp4"

    print("Downloading cover art.")
    download_file(cover_url, raw_cover_path)

    print("Creating full-height black-background video frame and YouTube thumbnail.")

    make_cover_image(
        raw_cover_path,
        video_frame_path,
        size=(1920, 1080),
        max_bytes=None,
    )

    make_cover_image(
        raw_cover_path,
        youtube_thumbnail_path,
        size=(1280, 720),
        max_bytes=2_000_000,
    )

    downloaded_audio = False

    if args.audio:
        audio_path = pathlib.Path(args.audio).expanduser().resolve()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        print(f"Using local audio: {audio_path}")
    else:
        print("Downloading best available SoundCloud audio.")
        audio_path = download_soundcloud_audio(args.soundcloud_url, outdir, stem)
        downloaded_audio = True

    print(f"Audio path: {audio_path}")
    print("Rendering video.")

    render_video(audio_path, video_frame_path, video_path)

    description = build_description(
        title=youtube_title,
        soundcloud_url=args.soundcloud_url,
        artist_url=artist_url,
    )

    print("Uploading to YouTube.")

    video_id = upload_video(
        youtube=youtube,
        video_path=video_path,
        title=youtube_title,
        description=description,
        privacy=args.privacy,
    )

    print(f"YouTube video ID: {video_id}")

    try:
        set_thumbnail(youtube, video_id, youtube_thumbnail_path)
        print("Thumbnail set.")
    except Exception as error:
        print(f"Warning: video uploaded, but thumbnail failed: {error}", file=sys.stderr)

    print(f"https://www.youtube.com/watch?v={video_id}")

    if not args.keep_temp:
        temp_files = [
            raw_cover_path,
            video_frame_path,
            youtube_thumbnail_path,
            video_path,
        ]

        if downloaded_audio:
            temp_files.append(audio_path)

        cleanup_temp_files(temp_files)
    else:
        print("Keeping temporary files because --keep-temp was used.")


if __name__ == "__main__":
    main()
