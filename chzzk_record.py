import asyncio
import hashlib
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aiofiles
import aiohttp
import orjson

if platform.system() != "Windows":
    import uvloop

    # Use uvloop for better performance
    uvloop.install()

def setup_logger() -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    
    file_handler = logging.FileHandler('log.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

print(
    "Chzzk Rekoda made by munsy0227\n"
    "If you encounter any bugs or errors, please report them on the Radiyu Shelter or GitHub issues!\n"
    "버그나 에러가 발생하면 라디유 쉘터나 깃허브 이슈에 제보해 주세요!"
)

# Constants
LIVE_DETAIL_API = "https://api.chzzk.naver.com/service/v2/channels/{channel_id}/live-detail"
TIME_FILE_PATH = Path('time_sleep.txt')
THREAD_FILE_PATH = Path('thread.txt')
CHANNELS_FILE_PATH = Path('channels.json')
DELAYS_FILE_PATH = Path('delays.json')
COOKIE_FILE_PATH = Path('cookie.json')
PLUGIN_DIR_PATH = Path('plugin')
MAX_FILENAME_BYTES = 150
SPECIAL_CHARS_REMOVER = re.compile(r"[\\/:*?\"<>|\u2600-\u26FF\u2700-\u27BF\u1F600-\u1F64F]")

async def setup_paths() -> Tuple[Path, Path]:
    os_name = platform.system()
    base_dir = Path(__file__).parent

    match os_name:
        case "Windows":
            streamlink_path = base_dir / "venv/Scripts/streamlink.exe"
            ffmpeg_path = base_dir / "ffmpeg/bin/ffmpeg.exe"
            logger.info("Running on Windows.")
        case "Linux" | "Darwin":
            streamlink_path = base_dir / "venv/bin/streamlink"
            ffmpeg_command = "which ffmpeg"
            try:
                process = await asyncio.create_subprocess_shell(
                    ffmpeg_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode == 0:
                    ffmpeg_path = Path(stdout.decode().strip())
                    logger.info(f"Running on {os_name}. ffmpeg found at: {ffmpeg_path}")
                else:
                    logger.error(f"ffmpeg not found in PATH on {os_name}.")
                    ffmpeg_path = None
            except Exception as e:
                logger.error(f"Error finding ffmpeg on {os_name}: {e}")
                ffmpeg_path = None
        case _:
            logger.error(f"Unsupported OS: {os_name}. Exiting.")
            await asyncio.sleep(5)
            exit()

    return streamlink_path, ffmpeg_path

async def load_json_async(file_path: Path) -> Any:
    async with aiofiles.open(file_path, "rb") as file:
        content = await file.read()
        return orjson.loads(content)

async def load_settings() -> Tuple[int, int, List[Dict[str, Any]], Dict[str, int]]:
    time_file_content = await load_json_async(TIME_FILE_PATH)
    timeout = time_file_content if isinstance(time_file_content, int) else int(time_file_content.get("timeout", 60))
    thread_file_content = await load_json_async(THREAD_FILE_PATH)
    stream_segment_threads = thread_file_content if isinstance(thread_file_content, int) else int(thread_file_content.get("threads", 2))
    channels = await load_json_async(CHANNELS_FILE_PATH)
    delays = await load_json_async(DELAYS_FILE_PATH)
    return timeout, stream_segment_threads, channels, delays

def get_auth_headers(cookies: Dict[str, str]) -> Dict[str, str]:
    return {
        'User-Agent': 'Mozilla/5.0 (X11; Unix x86_64)',
        'Cookie': f'NID_AUT={cookies.get("NID_AUT", "")}; NID_SES={cookies.get("NID_SES", "")}',
        'Origin': 'https://chzzk.naver.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Referer': ''
    }

async def get_session_cookies() -> Dict[str, str]:
    return await load_json_async(COOKIE_FILE_PATH)

async def get_live_info(channel: Dict[str, Any], headers: Dict[str, str], session: aiohttp.ClientSession) -> Dict[str, Any]:
    logger.debug(f"Fetching live info for channel: {channel['name']}")
    try:
        async with session.get(LIVE_DETAIL_API.format(channel_id=channel["id"]), headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
            logger.debug(f"Successfully fetched live info for channel: {channel['name']}, data: {data}")
            return data.get("content", {})
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error occurred while fetching live info for {channel['name']}: {e}")
    except Exception as e:
        logger.error(f"Failed to fetch live info for {channel['name']}: {e}")
    return {}

def shorten_filename(filename: str) -> str:
    if len(filename.encode('utf-8')) > MAX_FILENAME_BYTES:
        hash_value = hashlib.sha256(filename.encode()).hexdigest()[:8]
        name, extension = os.path.splitext(filename)
        shortened_name = f"{name[:MAX_FILENAME_BYTES - 75]}_{hash_value}{extension}"
        logger.warning(f"Filename {filename} is too long. Shortening to {shortened_name}.")
        return shortened_name
    return filename

def colorize_log(message: str, color_code: int) -> str:
    return f"\033[{color_code}m{message}\033[0m"

GREEN = 32
RED = 31

async def read_stream(stream: asyncio.StreamReader, channel_name: str, stream_type: str) -> None:
    summary = {}
    last_log_time = time.time()

    while True:
        line = await stream.readline()
        if not line:
            break
        line_str = line.decode().strip()

        if stream_type == "stderr" and line_str:
            if "Invalid DTS" in line_str or "Invalid PTS" in line_str:
                continue
            logger.debug(f"{channel_name} ffmpeg stderr: {line_str}")

        parts = line_str.split('=')
        if len(parts) == 2:
            key, value = parts
            summary[key.strip()] = value.strip()

        current_time = time.time()
        if 'progress' in summary and (current_time - last_log_time >= 5):
            total_size = summary.get('total_size', '0')
            total_size_formatted = format_size(int(total_size))
            log_message = (
                f"Bitrate={summary.get('bitrate', 'N/A')} "
                f"Total Size={total_size_formatted} "
                f"Out Time={summary.get('out_time', 'N/A')} "
                f"Speed={summary.get('speed', 'N/A')} "
                f"Progress={summary.get('progress', 'N/A')}"
            )
            colored_message = colorize_log(log_message, GREEN)
            logger.info(f"{channel_name} {stream_type}: {colored_message}")
            last_log_time = current_time
            summary.clear()

def format_size(size_bytes: int) -> str:
    if size_bytes < 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

async def record_stream(
    channel: Dict[str, Any],
    headers: Dict[str, str],
    session: aiohttp.ClientSession,
    delay: int,
    timeout: int,
    streamlink_path: Path,
    ffmpeg_path: Path,
    stream_segment_threads: int
) -> None:
    logger.info(f"Attempting to record stream for channel: {channel['name']}")
    await asyncio.sleep(delay)

    if channel.get("active", "on") == "off":
        logger.info(f"{channel['name']} channel is inactive. Skipping recording.")
        return

    recording_started = False
    stream_process = None
    ffmpeg_process = None

    while True:
        stream_url = f"https://chzzk.naver.com/live/{channel['id']}"
        if stream_url:
            logger.debug(f"Found stream URL for channel: {channel['name']}")
            try:
                cookies = await get_session_cookies()
                current_time = time.strftime("%Y-%m-%d_%H_%M_%S")
                channel_name = channel.get("name", "Unknown")
                live_info = await get_live_info(channel, headers, session)
                live_title = SPECIAL_CHARS_REMOVER.sub('', live_info.get("liveTitle", "").rstrip())
                output_dir = Path(channel.get("output_dir", "./recordings"))
                output_file = shorten_filename(f"[{current_time}] {channel_name} {live_title}.ts")
                output_path = output_dir / output_file

                output_dir.mkdir(parents=True, exist_ok=True)

                if not recording_started:
                    logger.info(f"Recording started for {channel_name} at {current_time}.")
                    recording_started = True

                if stream_process is not None and stream_process.returncode is None:
                    stream_process.kill()
                    await stream_process.wait()
                    logger.info("Existing stream process killed successfully.")

                if ffmpeg_process is not None and ffmpeg_process.returncode is None:
                    ffmpeg_process.kill()
                    await ffmpeg_process.wait()
                    logger.info("Existing ffmpeg process killed successfully.")

                rpipe, wpipe = os.pipe()

                stream_process = await asyncio.create_subprocess_exec(
                    streamlink_path, "--stdout", stream_url, "best", "--hls-live-restart",
                    "--plugin-dirs", PLUGIN_DIR_PATH,
                    "--stream-segment-threads", str(stream_segment_threads),
                    "--http-header", f'Cookie=NID_AUT={cookies.get("NID_AUT", "")}; NID_SES={cookies.get("NID_SES", "")}',
                    "--http-header", 'User-Agent=Mozilla/5.0 (X11; Unix x86_64)',
                    "--http-header", "Origin=https://chzzk.naver.com", "--http-header", "DNT=1",
                    "--http-header", "Sec-GPC=1", "--http-header", "Connection=keep-alive",
                    "--http-header", "Referer=", "--ffmpeg-ffmpeg", ffmpeg_path, "--ffmpeg-copyts", "--hls-segment-stream-data",
                    stdout=wpipe
                )
                os.close(wpipe)

                ffmpeg_process = await asyncio.create_subprocess_exec(
                    ffmpeg_path, "-i", "pipe:0", "-c", "copy", "-progress", "pipe:1", "-copy_unknown",
                    "-map_metadata:s:a", "0:g", "-bsf", "setts=pts=PTS-STARTPTS", "-y", output_path,
                    stdin=rpipe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                os.close(rpipe)

                stdout_task = asyncio.create_task(read_stream(ffmpeg_process.stdout, channel_name, "stdout"))
                stderr_task = asyncio.create_task(read_stream(ffmpeg_process.stderr, channel_name, "stderr"))

                await asyncio.gather(stdout_task, stderr_task, ffmpeg_process.wait())

                logger.info(f"ffmpeg process for {channel_name} exited with return code {ffmpeg_process.returncode}.")
                if recording_started:
                    logger.info(f"Recording stopped for {channel_name}.")
                    recording_started = False

                await stream_process.wait()
                logger.info(f"Stream recording process for {channel_name} exited with return code {stream_process.returncode}.")

            except Exception as e:
                logger.exception(f"Error occurred while recording {channel_name}: {e}")
                if recording_started:
                    logger.info(f"Recording stopped for {channel_name}.")
                    recording_started = False
        else:
            logger.error(f"No stream URL available for {channel['name']}")
            if recording_started:
                logger.info(f"Recording stopped for {channel_name}.")
                recording_started = False

        await asyncio.sleep(timeout)

async def main() -> None:
    timeout, stream_segment_threads, channels, delays = await load_settings()
    cookies = await get_session_cookies()
    headers = get_auth_headers(cookies)
    streamlink_path, ffmpeg_path = await setup_paths()
    async with aiohttp.ClientSession() as session:
        tasks = [
            record_stream(
                channel, headers, session, delays.get(channel.get("identifier"), 0),
                timeout, streamlink_path, ffmpeg_path, stream_segment_threads
            ) for channel in channels
        ]
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Recording stopped by user.")

if __name__ == "__main__":
    asyncio.run(main())

