"""One SCStream with Python callbacks for its screen/audio sample buffers."""

from __future__ import annotations

from typing import Any, Callable

import objc
import ScreenCaptureKit as SCK
from dispatch import dispatch_queue_create
from Foundation import NSObject

from screen_recorder_mcp.backends.macos.sck_content import wait_for_completion

SampleCallback = Callable[[Any], None]
StoppedCallback = Callable[[Any], None]


class SampleBufferOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
    """Routes SCK sample buffers to Python callbacks by output type."""

    def initWithScreen_audio_(self, screen: SampleCallback | None, audio: SampleCallback | None):  # noqa: N802
        self = objc.super(SampleBufferOutput, self).init()
        if self is None:
            return None
        self.screen_callback = screen
        self.audio_callback = audio
        return self

    def stream_didOutputSampleBuffer_ofType_(self, _stream: Any, sample_buffer: Any, output_type: int) -> None:  # noqa: N802
        callback = self.screen_callback if output_type == SCK.SCStreamOutputTypeScreen else self.audio_callback
        if callback is not None:
            callback(sample_buffer)


class StreamStopObserver(NSObject, protocols=[objc.protocolNamed("SCStreamDelegate")]):
    """Reports SCK stopping on its own, e.g. because the captured window closed."""

    def initWithCallback_(self, stopped: StoppedCallback):  # noqa: N802
        self = objc.super(StreamStopObserver, self).init()
        if self is None:
            return None
        self.stopped_callback = stopped
        return self

    def stream_didStopWithError_(self, _stream: Any, error: Any) -> None:  # noqa: N802
        self.stopped_callback(error)


class CaptureStream:
    """Owns one SCStream: build, start, stop, all blocking.

    Example:
        stream = CaptureStream(content_filter, configuration, screen=on_frame, audio=None,
                               stopped=on_stop)
        stream.start(); ...; stream.stop()
    """

    def __init__(
        self,
        content_filter: Any,
        configuration: Any,
        screen: SampleCallback | None,
        audio: SampleCallback | None,
        stopped: StoppedCallback,
    ) -> None:
        # Keep strong references: SCK holds the output/delegate weakly.
        self._output = SampleBufferOutput.alloc().initWithScreen_audio_(screen, audio)
        self._observer = StreamStopObserver.alloc().initWithCallback_(stopped)
        self._queue = dispatch_queue_create(b"screen-recorder-mcp.sck", None)  # None = serial
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, configuration, self._observer
        )
        if screen is not None:
            self._add_output(SCK.SCStreamOutputTypeScreen)
        if audio is not None:
            self._add_output(SCK.SCStreamOutputTypeAudio)

    def start(self) -> None:
        (error,) = wait_for_completion(self._stream.startCaptureWithCompletionHandler_, "start")
        if error is not None:
            raise RuntimeError(f"SCStream failed to start: {error}")

    def stop(self) -> None:
        # An error or no answer means the stream already stopped (window closed).
        try:
            wait_for_completion(self._stream.stopCaptureWithCompletionHandler_, "stop")
        except TimeoutError:
            pass

    def _add_output(self, output_type: int) -> None:
        ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, output_type, self._queue, None
        )
        if not ok:
            raise RuntimeError(f"SCStream refused output type {output_type}: {error}")
