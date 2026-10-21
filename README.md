# screen-recorder-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant record a video of one app
window, a part of it, or a whole monitor. Works on Windows and macOS.

Window recordings only contain the chosen app. If another window is dragged over it, the recording
keeps showing the app underneath, not the window on top.

```
You:    record the Chrome window for the demo, just the left half
Claude: [calls list_targets, pick_region, start_recording]
        Recording Chrome. Tell me when to stop.
You:    stop
Claude: Saved ~/Videos/screen-recorder-mcp/rec-20261021-153000.mp4 (42 s)
```

## Features

- Record a single window (by title, app name or id) or a whole monitor.
- Windows in front of the recorded app don't show up in the video.
- Crop: an overlay lets you drag a rectangle over the app to record only that part.
- Optional audio, off by default: system sound or microphone.
- Constant frame rate MP4 (H.264), even when the app isn't redrawing.
- Works without the GPU on Windows: if the GPU can't start a capture session (out of video memory,
  busy with a render or a game), it falls back to CPU capture on its own. You can also force it.
- Plain stdio MCP server, so any MCP client can use it (Claude Code, Claude Desktop, Cursor, ...).

## Platform support

| | Windows 10 2004+ / 11 | macOS 13 Ventura+ |
|---|---|---|
| Capture API | Windows Graphics Capture, GDI `PrintWindow` on CPU | ScreenCaptureKit |
| Window recording, ignores apps in front | yes | yes |
| `audio="system"` | everything the PC plays | only the recorded app (all apps for a monitor) |
| `audio="mic"` | default microphone | default microphone |
| Status | tested | **experimental**, not yet tested on real hardware |

Linux isn't supported yet. The OS-specific code sits behind one interface
(`src/screen_recorder_mcp/backends/`), so adding it is mostly a matter of writing a new backend.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` on your PATH
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
- The region picker uses tkinter. uv's managed Python includes it; Homebrew's Python often doesn't.

## Install

```bash
git clone https://github.com/gcamara/screen-recorder-mcp.git
cd screen-recorder-mcp
uv sync
```

### macOS permissions

Give **Screen Recording** permission to the app that launches the server (Terminal, iTerm, the
Claude desktop app, ...) in System Settings > Privacy & Security, then restart that app.
`audio="mic"` also needs **Microphone**.

## Connect it to your AI client

Replace `/path/to/screen-recorder-mcp` with where you cloned it.

**Claude Code**

```bash
claude mcp add screen-recorder --scope user -- uv --directory /path/to/screen-recorder-mcp run screen-recorder-mcp
```

**Claude Desktop, Cursor and other clients** (`mcpServers` section of the client's config):

```json
{
  "mcpServers": {
    "screen-recorder": {
      "command": "uv",
      "args": ["--directory", "/path/to/screen-recorder-mcp", "run", "screen-recorder-mcp"]
    }
  }
}
```

Restart the client and the five tools below show up.

## Usage

Just ask in plain language:

- "Record the Notepad window for 30 seconds."
- "Record my second monitor with the microphone on."
- "Record the YouTube tab." The site is matched against the browser window's title, so that tab has
  to be the active one.
- "Start recording." The assistant lists your windows and asks which one, then asks whether you want
  to crop.

The server tells the assistant to:

1. use the screen, app or site you named, or ask you which one when you didn't;
2. ask whether you want to record only part of it, and if so open the crop overlay;
3. leave audio off unless you asked for it.

**Crop overlay:** drag a rectangle, then press Enter or double-click to confirm. Esc or right-click
records the whole window.

### Tools

| Tool | What it does |
|---|---|
| `list_targets()` | Visible windows (front-most first) and monitors (`monitor:1`, `monitor:2`, ...) |
| `pick_region(target)` | Opens the crop overlay over the target and returns the rectangle you drew (or `null`) |
| `start_recording(target, region?, fps=30, audio="none", capture_cursor=true, output_dir?, capture="auto")` | Starts recording |
| `recording_status()` | Elapsed time, frames written, whether the target window was closed |
| `stop_recording()` | Finishes the file and returns its path and size |

`target` accepts a window title or part of it, an app name (`chrome`, `notepad.exe`, `Safari`), a
`window_id` from `list_targets`, or `monitor:N`.

`capture` picks how frames are read: `"auto"` (GPU, falling back to CPU), `"gpu"`, or `"cpu"`
(Windows only). Ask for it in plain words, e.g. "record Chrome, use the CPU, my GPU is busy". CPU
capture doesn't draw the mouse cursor and costs more CPU per frame.

Files are saved to `~/Videos/screen-recorder-mcp/` on Windows and `~/Movies/screen-recorder-mcp/` on
macOS, unless you pass `output_dir`.

## Limitations

- A minimized window produces no frames. `start_recording` fails with a message saying so.
- On Windows, `audio="system"` records every app's sound, not only the recorded one.
- Recording a whole monitor captures whatever is on it, including windows on top.
- Some apps block capture of their windows (DRM video, some password managers). Those come out black.
- One recording at a time.

## How it works

```
OS capture API ──frames on change──▶ latest-frame slot ──▶ pacer (fixed fps) ──▶ ffmpeg ──▶ MP4
 (WGC / SCK)                                                  crop + size lock
optional audio ──▶ WAV (silence-padded) ─────────────────────────────── mux ──▶ final MP4
```

- **Per-window capture.** Windows Graphics Capture and ScreenCaptureKit both read the window's own
  surface from the compositor, not the pixels on screen. That's why windows on top don't appear.
  The CPU path on Windows asks the window to paint itself into a bitmap (`PrintWindow`), which keeps
  the same guarantee, and stretches windows that aren't DPI-aware the way the desktop shows them.
- **Constant frame rate.** Both APIs only send a frame when the window changes, so a static window
  would produce a 10-second video only a fraction of a second long. A pacer thread re-sends the
  latest frame on a fixed clock.
- **Size lock.** The output size is fixed from the first frame. If the window is resized later,
  frames are padded or trimmed to that size, because ffmpeg's raw input needs a constant size.
- **Audio gaps.** System/app audio sends no packets while nothing is playing. The WAV writer fills
  those gaps with silence so the audio stays in sync with the video.
- **stdout stays clean.** In a stdio MCP server stdout carries the protocol. ffmpeg's output goes to
  a log file, and the crop overlay runs in its own process.

## Development

```bash
uv run pytest -q                                      # unit tests, no desktop needed
uv run python scripts/verify_recording.py notepad     # real recording on this machine
uv run python scripts/verify_recording.py notepad --capture=cpu
```

`verify_recording.py` records a window while a red window covers it, then checks the result with
ffprobe: duration, even dimensions, no red in the frames, and an audio stream when audio is on.
On macOS use `TextEdit` as the window. If you have a Mac, running it there and reporting the output
is the most useful contribution right now.

Layout:

```
src/screen_recorder_mcp/
  server.py            MCP tools
  recorder.py          one recording session (capture → pacer → encoder, audio, mux)
  frame_pacer.py       constant-fps pacing
  region_picker.py     crop overlay (runs as a subprocess)
  frame_sources.py     polling source + GPU→CPU fallback
  backends/windows/    Win32 window list, WGC capture, GDI CPU capture, WASAPI audio
  backends/macos/      Quartz window list, ScreenCaptureKit capture and app audio
```

## License

[MIT](LICENSE)
