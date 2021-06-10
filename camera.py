import logging
import os
import threading
import time
import glob
from io import BytesIO

import decorator
import requests
from numpy import random
import cv2
from PIL import Image


# Enable logging
class Camera:
    def __init__(self, moonraker_host: str, host: str, threads: int = 0, light_device: str = "", light_enable: bool = False, light_timeout: int = 0, flip_vertically: bool = False,
                 flip_horisontally: bool = False, fourcc: str = 'x264', gif_duration: int = 5, reduce_gif: int = 2, video_duration: int = 10, imgs: str = ""):
        self._host: str = host
        self._threads: int = threads
        self._flipVertically: bool = flip_vertically
        self._flipHorisontally: bool = flip_horisontally
        self._fourcc: str = fourcc
        self._gifDuration: int = gif_duration
        self._reduceGif: int = reduce_gif
        self._videoDuration: int = video_duration
        self._imgs: str = imgs
        self._moonraker_host: str = moonraker_host
        self._light_state_lock = threading.Lock()
        self._light_device_on: bool = False
        self.light_need_off: bool = False

        self.light_enable: bool = light_enable
        self.light_timeout: int = light_timeout
        # Todo: make class for power device
        self.light_device: str = light_device
        self.camera_lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event = threading.Event()
        self.light_timer_event.set()

    @property
    def light_state(self):
        with self._light_state_lock:
            return self._light_device_on

    @light_state.setter
    def light_state(self, state: bool):
        with self._light_state_lock:
            self._light_device_on = state

    def togle_ligth_device(self):
        self.switch_ligth_device(not self.light_state)

    def switch_ligth_device(self, state: bool):
        with self._light_state_lock:
            if state:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.light_device}&action=on")
                if res.ok:
                    self._light_device_on = True
            else:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.light_device}&action=off")
                if res.ok:
                    self._light_device_on = False

    # @decorator.decorator
    def cam_ligth_toogle(func):
        def wrapper(self, *args, **kwargs):
            if self.light_enable and self.light_device and not self.light_state and not self.light_lock.locked():
                self.light_timer_event.clear()
                self.light_lock.acquire()
                self.light_need_off = True
                self.switch_ligth_device(True)
                time.sleep(self.light_timeout)
                self.light_timer_event.set()

            self.light_timer_event.wait()

            result = func(self, *args, **kwargs)

            if self.light_enable and self.light_device and self.light_need_off:
                if self.light_lock.locked():
                    self.light_lock.release()
                if not self.camera_lock.locked() and not self.light_lock.locked():
                    self.light_need_off = False
                    self.switch_ligth_device(False)

            return result

        return wrapper

    @cam_ligth_toogle
    def take_photo(self) -> BytesIO:
        with self.camera_lock:
            cap = cv2.VideoCapture(self._host)

            success, image = cap.read()

            if not success:
                img = Image.open(random.choice(glob.glob(f'{self._imgs}/imgs/*.jpg')))
            else:
                img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                if self._flipVertically:
                    img = img.transpose(Image.FLIP_TOP_BOTTOM)
                if self._flipHorisontally:
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)

        bio = BytesIO()
        bio.name = 'status.jpeg'
        img.save(bio, 'JPEG', quality=75, subsampling=0)
        bio.seek(0)
        return bio

    @cam_ligth_toogle
    def take_video(self):
        def process_video_frame(frame_loc):
            if self._flipVertically and self._flipHorisontally:
                frame_loc = cv2.flip(frame_loc, -1)
            elif self._flipHorisontally:
                frame_loc = cv2.flip(frame_loc, 1)
            elif self._flipVertically:
                frame_loc = cv2.flip(frame_loc, 0)

            return frame_loc

        with self.camera_lock:
            cv2.setNumThreads(self._threads)
            cap = cv2.VideoCapture(self._host)
            success, frame = cap.read()

            # if not success:
            #     message_to_reply.reply_text("camera connection failed!")
            #     return

            height, width, channels = frame.shape
            fps_video = cap.get(cv2.CAP_PROP_FPS)
            fps = 10
            filepath = os.path.join('/tmp/', 'video.mp4')
            out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=fps_video, frameSize=(width, height))
            t_end = time.time() + self._videoDuration
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, frame_inner = cap.read()
                out.write(process_video_frame(frame_inner))
                fps = 1 / (time.time() - prev_frame_time)

            out.set(cv2.CAP_PROP_FPS, fps)
            out.release()

        # message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)

        bio = BytesIO()
        bio.name = 'video.mp4'
        with open(filepath, 'rb') as fh:
            bio.write(fh.read())

        os.remove(filepath)
        bio.seek(0)

        return bio, width, height

    @cam_ligth_toogle
    def take_gif(self):
        def process_frame(frame) -> Image:
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if self._flipVertically:
                img = img.transpose(Image.FLIP_TOP_BOTTOM)
            if self._flipHorisontally:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            if self._reduceGif > 0:
                img = img.resize((int(width / self._reduceGif), int(height / self._reduceGif)))
            return img

        gif = []
        fps = 0
        with self.camera_lock:
            cv2.setNumThreads(self._threads)
            cap = cv2.VideoCapture(self._host)
            success, image = cap.read()

            # if not success:
            #     message_to_reply.reply_text("camera connection failed!")
            #     return

            height, width, channels = image.shape
            gif.append(process_frame(image))

            t_end = time.time() + self._gifDuration
            # TOdo: calc frame count
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, image_inner = cap.read()
                new_frame_time = time.time()
                gif.append(process_frame(image_inner))
                fps = 1 / (new_frame_time - prev_frame_time)

        # message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
        if fps <= 0:
            fps = 1
        bio = BytesIO()
        bio.name = 'image.gif'
        gif[0].save(bio, format='GIF', save_all=True, optimize=True, append_images=gif[1:], duration=int(1000 / int(fps)), loop=0)
        bio.seek(0)

        return bio, width, height