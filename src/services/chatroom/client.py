import urwid
import aiorpcx
import asyncio
from datetime import datetime, timedelta
from copy import deepcopy
import json
import os
import re
import traceback
from dataclasses import dataclass
import yaml
from functools import partial
from copy import deepcopy
import sys
from PIL import Image
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

pool_executor = ThreadPoolExecutor(max_workers=8)

this_file_path = os.path.dirname(os.path.abspath(__file__))

HEART_TRACE = False
if HEART_TRACE:
    import heartrate
    heartrate.trace(browser=True)

config = None
try:
    with open(os.path.join(os.path.dirname(__file__), 'config.yaml'), 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception as exc:
    print(f"读取config失败: {exc}")
    exit(1)
    
CLIENT_NAME = 'chatclient'  
FILE_DB_PATH = config['file_db_path']
SERVER_HOST = config['server']['host']
SERVER_PORT = config['server']['port']
SERVER_TOKEN = config['server']['token']
SERVER_LOG_URL = config['server']['log_url']
RECONNECT_INTERVAL = 5
UPDATE_INTERVAL = 1 / 15
MSG_GET_INTERVAL = 0.5
REPLY_MSG_LEN_LIMIT = 32
CMD_HISTORY_LIMIT = 100
MSG_HISTORY_LIMIT = 100
HEARTBEAT_INTERVAL = 5
HEARTBEAT_FAIL_LIMIT = 3
RPC_TIMEOUT = 10
IMAGE_OPEN_COMMAND = 'code {local_image_path}'
SERVER_LOG_LINE_LIMIT = 100
SPLIT_SEND_MSG_LEN = 2 ** 18

PALLETE = [
    ('button_label', '', ''),
]

# ------------------------------------ 工具 ------------------------------------ #

class PeriodControl:
    def __init__(self, interval):
        self.interval = interval
    
    async def __aenter__(self):
        self.start_time = datetime.now()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed < self.interval:
            await asyncio.sleep(self.interval - elapsed)

class FileDB:
    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, 'r') as f:
                self.data = json.load(f)
        except:
            self.data = {}

    def keys(self):
        return self.data.keys()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def get(self, key: str, default=None):
        """
        - 获取某个key的值，找不到返回default
        - 直接返回缓存对象，若要进行修改又不影响DB内容则必须自行deepcopy
        """
        assert isinstance(key, str), f'key必须是字符串，当前类型: {type(key)}'
        return self.data.get(key, default)

    def get_copy(self, key: str, default=None):
        assert isinstance(key, str), f'key必须是字符串，当前类型: {type(key)}'
        return deepcopy(self.data.get(key, default))

    def set(self, key, value):
        self.data[key] = deepcopy(value)
        self.save()

    def delete(self, key):
        if key in self.data:
            del self.data[key]
            self.save()

local_file_db = FileDB(FILE_DB_PATH)
TMP_FILE_FOLDER = local_file_db.get('tmp_file_folder', os.path.join(this_file_path, 'cache'))

def find_key_recursive(d, key):
    if key in d:
        return d[key]
    for k, v in d.items():
        if isinstance(v, dict):
            ret = find_key_recursive(v, key)
            if ret:
                return ret
    return None

def limit_str_len(s, limit):
    # 中文字符长度为2
    # length = 0
    # for c in s:
    #     if ord(c) > 127:
    #         length += 2
    #     else:
    #         length += 1
    #     if length > limit:
    #         return s[:limit] + '...'
    if len(s) > limit:
        return s[:limit] + '...'
    return s

def test_func(*args):
    log_box.add_line(f"test_func: {args}")

def exit_func():
    raise urwid.ExitMainLoop()

def open_hyperlink(link):
    import webbrowser
    webbrowser.open(link)

async def get_local_image_path(key, url=None):
    image_path = os.path.join(TMP_FILE_FOLDER, 'images', f'{key}.jpg')
    if not os.path.exists(image_path):
        if not url:
            log_box.add_line(f"图片 {image_path} 不存在")
            return None
        log_box.add_line(f"图片 {image_path} 不存在，下载图片: {url}")
        def download_image(url, image_path):
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            resp = requests.get(url, timeout=10)
            if resp.status_code!= 200:
                log_box.add_line(f"下载图片失败: {url} {resp.status_code}")
                return None
            with open(image_path, 'wb') as f:
                f.write(resp.content)
        await asyncio.get_event_loop().run_in_executor(pool_executor, download_image, url, image_path)
    return image_path

async def open_image_by_url(url):
    key = get_md5(url)
    image_path = await get_local_image_path(key, url)
    if image_path:
        os.system(IMAGE_OPEN_COMMAND.format(local_image_path=image_path))

async def open_image(seg):
    url = seg['data']['url']
    key = get_key_from_image_seg(seg)
    image_path = await get_local_image_path(key, url)
    if image_path:
        os.system(IMAGE_OPEN_COMMAND.format(local_image_path=image_path))

async def click_to_open_image(group_id, message_id, seg_ind):
    try:
        msg = await rpc_get_msg(group_id, message_id)
        # log_box.add_line(f"打开图片: {message_id} {seg_ind} {msg}")
        seg = msg['msg'][seg_ind]
        if seg['type'] != 'image':
            raise Exception(f"消息 {message_id} 第 {seg_ind} 个seg不是图片")
        await open_image(seg)
    except Exception as e:
        log_box.add_line(f"打开图片失败: {e}")

def add_content_to_cmdline(content, clear_content):
    if clear_content:
        cmd_line.set_edit_text(content)
        cmd_line.set_edit_pos(len(content))
    else:
        cmd_line.set_edit_text(cmd_line.get_edit_text() + content)
        cmd_line.set_edit_pos(len(cmd_line.get_edit_text()))
    layout.set_focus('footer')

last_click_to_send_time = datetime.now()
def double_click_to_send():
    global last_click_to_send_time
    now = datetime.now()
    if (now - last_click_to_send_time).total_seconds() < 0.5:
        add_content_to_cmdline(f"raw[{cmd_line.get_edit_text()}]", True)
        cmd_line.enter()
    last_click_to_send_time = now

last_click_face_to_send_time = datetime.now()
last_click_face = None
async def double_click_face_to_send(face):
    global last_click_face_to_send_time
    global last_click_face
    now = datetime.now()
    if face == last_click_face and (now - last_click_face_to_send_time).total_seconds() < 0.5:
        show_hide_face_bar()
        await send_msg(f"#{face}")
    last_click_face_to_send_time = now
    last_click_face = face

async def run_code_async(code):
    tmp_code_path = os.path.join(TMP_FILE_FOLDER, 'tmp_code.py')
    with open(tmp_code_path, "w") as f:
        f.write(code)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, tmp_code_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return proc, stdout.decode(), stderr.decode()

async def select_image_file():
    code = r"""
from tkinter import filedialog
file_path = filedialog.askopenfilename()
image_suffix = ('.jpg', '.jpeg', '.png', '.bmp', '.gif')
if not file_path.endswith(image_suffix):
    print(" ")
else:
    print(file_path)
"""
    proc, file_path, err = await run_code_async(code)
    try:
        if not file_path.strip():
            log_box.add_line("未选择图片")
            return
        add_content_to_cmdline(f"pic[{file_path.strip()}]", False)
    finally:
        try:
            proc.kill()
        except:
            pass

async def update_server_log(clear_content=True):
    if clear_content:
        server_log_box.clear_lines()
        server_log_box.add_line("正在获取日志...")
    import aiohttp
    url = f"{SERVER_LOG_URL}?lines={SERVER_LOG_LINE_LIMIT}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, verify_ssl=False) as resp:
            if resp.status != 200:
                log_box.add_line(f"获取日志失败: {resp.status}")
            text = await resp.text()
            server_log_box.clear_lines()
            for line in text.split('\n'):
                server_log_box.add_line(line)

def get_image_cq(image_path):
    with open(image_path, 'rb') as f:
        b64_image = base64.b64encode(f.read()).decode()
        return f"[CQ:image,file=base64://{b64_image}]"

def get_md5(s):
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()

# ------------------------------------ UI  ------------------------------------ #

def get_text_len(text):
    len = 0
    # 中文字符长度为2
    for c in text:
        if ord(c) > 127:
            len += 2
        else:
            len += 1
    return len

def get_hyperlink_str(text, link):
    data = json.dumps({'type': 'hyperlink', 'text': text, 'link': link}, ensure_ascii=False)
    return f'#$#{data}#$#'

def get_button_str(text, callback_func, args=[]):
    data = json.dumps({'type': 'button', 'text': text, 'callback_name': callback_func.__name__, 'args': args}, ensure_ascii=False)
    return f'#$#{data}#$#'

def get_image_str(text, msg_id=None, seg_ind=None, link=None):
    data = json.dumps({'type': 'image', 'text': text, 'msg_id': msg_id, 'seg_ind': seg_ind, 'link': link}, ensure_ascii=False)
    return f'#$#{data}#$#'

def button_callback_handler(callback_func, args, widget):
    # log_box.add_line(f"按钮点击: {callback_func.__name__} {args}")
    # return
    if asyncio.iscoroutinefunction(callback_func):
        aloop.create_task(callback_func(*args))
    else:
        callback_func(*args)

class SelectableText(urwid.Text):
    _selectable = True
    def keypress(self, size, key):
        return key

class CustomButton(urwid.Button):
    button_left = urwid.Text("")
    button_right = urwid.Text("") 
    def __init__(self, label, callback_func, callback_args=[]):
        callback_args = deepcopy(callback_args)
        super().__init__("", None, None)
        self._label = urwid.SelectableIcon(('button_label', label), 0)
        self._w = urwid.AttrMap(urwid.Columns(
            [('fixed', 0, self.button_left), self._label, ('fixed', 0, self.button_right)],
            dividechars=0
        ), None, focus_map="reversed")
        func = partial(button_callback_handler, callback_func, callback_args)
        urwid.connect_signal(self, 'click', func)

    def selectable(self) -> bool:
        return False

class CommandLine(urwid.Edit):
    def __init__(self):
        super().__init__(caption="> ")
        self.history = local_file_db.get('cmd_history', [])
        self.history_pos = len(self.history)
        self.queue = asyncio.Queue()

    def clear(self):
        self.set_edit_text("")

    def enter(self):
        command = self.get_edit_text()
        if command:
            self.history.append(command)
            if len(self.history) > CMD_HISTORY_LIMIT:
                self.history = self.history[-CMD_HISTORY_LIMIT:]
            self.history_pos = len(self.history)
            local_file_db.set('cmd_history', self.history)
            self.set_edit_text("")
        self.queue.put_nowait(command)

    def keypress(self, size, key):
        if key == 'enter':
            self.enter()
        elif key == 'up':
            self.history_pos = max(0, self.history_pos - 1)
            if self.history:
                self.set_edit_text(self.history[self.history_pos])
                self.set_edit_pos(len(self.history[self.history_pos]))
        elif key == 'down':
            self.history_pos = min(len(self.history), self.history_pos + 1)
            if self.history_pos < len(self.history):
                self.set_edit_text(self.history[self.history_pos])
                self.set_edit_pos(len(self.history[self.history_pos]))
            else:
                self.set_edit_text("")
        else:
            return super().keypress(size, key)
        
class TextBox(urwid.ListBox):
    def __init__(self, name=None):
        self.name = name
        self.lines = urwid.SimpleFocusListWalker([])
        super().__init__(self.lines)

    def content_len(self):
        if self.lines:
            return len(self.lines)
        return 0

    def set_focus_pos(self, pos):
        if self.content_len() > 0:
            pos = max(0, min(pos, self.content_len() - 1))
            self.set_focus(pos)

    def get_focus_pos(self):
        if self.get_focus() and self.get_focus()[1]:
            return self.get_focus()[1]
        return 0

    def set_bottom_focus(self):
        self.set_focus_pos(self.content_len() - 1)
    
    def set_top_focus(self):
        self.set_focus_pos(0)

    def is_bottom_focus(self):
        return self.get_focus_pos() == self.content_len() - 1

    def add_line(self, text='', keep_bottom_focus=True):
        is_bottom = self.is_bottom_focus()
        segs = []
        for seg in text.split('#$#'):
            if not seg:
                continue

            try:
                data = json.loads(seg)
                assert isinstance(data, dict) and 'type' in data
            except:
                segs.append(urwid.Text(seg))
                continue

            try:
                if data['type'] == 'hyperlink':
                    segs.append(CustomButton(data['text'], open_hyperlink, [data['link']]))

                if data['type'] == 'button':
                    segs.append(CustomButton(data['text'], globals()[data['callback_name']], data['args']))

                if data['type'] == 'image':
                    if data.get('link') is not None:
                        segs.append(CustomButton(data['text'], open_image_by_url, [data['link']]))
                    else:
                        segs.append(CustomButton(data['text'], click_to_open_image, [gdata.cur_group_id, data['msg_id'], data['seg_ind']]))

            except Exception as exc:
                log_box.add_line(f"TextLine {data} 解析失败:")
                log_box.add_line(f"{traceback.format_exc()}")
                segs.append(urwid.Text(seg))

        if len(segs) == 0:
            self.lines.append(urwid.Text(""))
        else:
            new_col = urwid.Columns([], dividechars=0)
            contents = [(seg, new_col.options(width_type='pack')) for seg in segs]
            new_col.contents = contents
            self.lines.append(new_col)

        if keep_bottom_focus and is_bottom:
            self.set_focus_pos(len(self.lines) - 1)

    def clear_lines(self):
        self.lines.clear()

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        # if key == 'down':
        #     self.set_focus_pos(self.get_focus_pos() + 1)
        # elif key == 'up':
        #     self.set_focus_pos(self.get_focus_pos() - 1)
        if key == 'ctrl b':
            self.set_bottom_focus()
        else:
            return super().keypress(size, key)

title = urwid.Columns([])
tab_bars = urwid.Pile([])
footer_split = urwid.Columns([])
btn_bar = urwid.Columns([])
cmd_line = CommandLine()
chat_box = TextBox("chat_box")
forward_chat_box = TextBox("forward_chat_box")
face_bar = urwid.Pile([])
log_box = TextBox("log_box")
server_log_box = TextBox("server_log_box")
layout = urwid.Frame(
    header = urwid.Pile([title, tab_bars]),
    body = urwid.Columns([
        ('weight', 75, chat_box),
        ('fixed',  1, urwid.SolidFill('|')),
        ('weight', 25, log_box)
    ]),
    footer = urwid.Pile([footer_split, btn_bar, cmd_line]),
    focus_part = 'footer'
)

def set_chatbox_weight(chat_box_weight, verbose=False):
    hide_server_log_box()
    chat_box_weight = max(0, min(100, chat_box_weight))
    gdata.chat_box_weight = chat_box_weight
    local_file_db.set('chat_box_weight', gdata.chat_box_weight)
    if verbose:
        log_box.add_line(f"设置比例: {chat_box_weight}:{100 - chat_box_weight}")
    if chat_box_weight == 0:
        layout.body = log_box
    elif chat_box_weight == 100:
        layout.body = chat_box
    else:
        layout.body = urwid.Columns([
            ('weight', chat_box_weight, chat_box),
            ('fixed',  1, urwid.SolidFill('|')),
            ('weight', 100 - chat_box_weight, log_box)
        ])

def fold_or_unfold_logbox():
    dst_chat_box_weight = 50 if gdata.chat_box_weight >= 90 else 100
    set_chatbox_weight(dst_chat_box_weight, verbose=False)

def fold_logbox():
    set_chatbox_weight(100, verbose=False)

def unfold_logbox():
    set_chatbox_weight(50, verbose=False)

def show_hide_face_bar():
    global layout
    if len(layout.footer.contents) == 3:
        layout.footer = urwid.Pile([footer_split, face_bar, btn_bar, cmd_line])
    else:
        layout.footer = urwid.Pile([footer_split, btn_bar, cmd_line])

def show_forward_chat_box():
    global layout
    if isinstance(layout.body, TextBox) and layout.body == chat_box:
        layout.body = forward_chat_box
    if isinstance(layout.body, urwid.Columns) and layout.body.contents[0][0] == chat_box:
        layout.body.contents[0] = (forward_chat_box, layout.body.contents[0][1])
    if len(footer_split.contents) == 0:
        return_btn = CustomButton("[返回]", hide_forward_chat_box)
        footer_split.contents.append((return_btn, footer_split.options(width_type='pack')))

def hide_forward_chat_box():
    global layout
    if isinstance(layout.body, TextBox) and layout.body == forward_chat_box:
        layout.body = chat_box
    if isinstance(layout.body, urwid.Columns) and layout.body.contents[0][0] == forward_chat_box:
        layout.body.contents[0] = (chat_box, layout.body.contents[0][1])
    if len(footer_split.contents) > 0:
        footer_split.contents.clear()

last_body_widget = None
def show_server_log_box():
    global layout, last_body_widget
    if layout.body != server_log_box:
        last_body_widget = layout.body
        layout.body = server_log_box
        aloop.create_task(update_server_log())

def hide_server_log_box():
    global layout, last_body_widget
    if layout.body == server_log_box:
        layout.body = last_body_widget

def show_hide_server_log_box():
    if layout.body == server_log_box:
        hide_server_log_box()
    else:
        show_server_log_box()

def refresh():
    # 刷新群组或者日志
    if layout.body == server_log_box:
        aloop.create_task(update_server_log())
    else:
        aloop.create_task(enter_group(None))

def clear_footer_split_text(seconds=None):
    if seconds is None:
        footer_split.contents.clear()
    else:
        aloop.call_later(seconds, lambda: footer_split.contents.clear())

def set_footer_split_text(text, continue_seconds=None):
    footer_split.contents.clear()
    footer_split.contents.append((urwid.Text(f"* {text}"), footer_split.options(width_type='pack')))
    if continue_seconds:
        clear_footer_split_text(continue_seconds)

# ------------------------------------ RPC ------------------------------------ #

class Session:
    def __init__(self, host, port, token):
        self.host = host
        self.port = port
        self.token = token
        self.session = None
        self.ws_client = None
    
    def is_connected(self):
        return self.session is not None

    async def connect(self):
        ws = aiorpcx.connect_ws(f'ws://{self.host}:{self.port}') 
        self.session = await ws.__aenter__()
        self.ws_client = ws
    
    async def close(self):
        if self.session:
            await self.ws_client.__aexit__(None, None, None)
            self.session = None
            self.ws_client = None
    
    async def send_request(self, method, params=None, default=None, timeout=RPC_TIMEOUT):
        try:
            if params is None:
                params = []
            params = [self.token] + params
            return await asyncio.wait_for(self.session.send_request(method, params), timeout)
        except Exception as exc:
            self.session = None
            log_box.add_line(f"请求失败: {exc}")
            log_box.add_line(f"{traceback.format_exc()}")
            return default
        
session = Session(SERVER_HOST, SERVER_PORT, SERVER_TOKEN)

def require_connected(func):
    if not asyncio.iscoroutinefunction(func):
        def wrapper(*args, **kwargs):
            if session.is_connected():
                return func(*args, **kwargs)
            else:
                log_box.add_line("未连接到服务器")
        return wrapper
    else:
        async def wrapper(*args, **kwargs):
            if session.is_connected():
                return await func(*args, **kwargs)
            else:
                log_box.add_line("未连接到服务器")
        return wrapper

@require_connected
async def rpc_get_group_list():
    global session
    return await session.send_request('get_group_list', [], default=[])

@require_connected
async def rpc_get_group_new_msg(group_id):
    global session
    return await session.send_request('get_group_new_msg', [group_id], default=[])

@require_connected
async def rpc_get_group_history_msg(group_id, limit):
    global session
    return await session.send_request('get_group_history_msg', [group_id, limit], default=[])

@require_connected
async def rpc_send_group_msg(group_id, msg):
    global session
    set_footer_split_text("消息发送中...")
    ret = await session.send_request('send_group_msg', [group_id, msg])
    clear_footer_split_text()
    return ret

@require_connected
async def rpc_get_client_data(name):
    global session
    return await session.send_request('get_client_data', [name], default={})

@require_connected
async def rpc_set_client_data(name, data):
    global session
    return await session.send_request('set_client_data', [name, data])

@require_connected
async def rpc_get_msg(group_id, msg_id):
    global session
    return await session.send_request('get_msg', [group_id, msg_id])

@require_connected
async def rpc_get_forward_msg(group_id, forward_id):
    global session
    return await session.send_request('get_forward_msg', [group_id, forward_id])

@require_connected
async def rpc_send_group_msg_split(group_id, msg):
    is_str = isinstance(msg, str)
    if not is_str:
        # parse to CQ
        tmp = ""
        for seg in msg:
            if seg['type'] == 'text':
                tmp += seg['data']['text']
            else:
                tmp += f"[CQ:{seg['type']},"
                for k, v in seg['data'].items():
                    tmp += f"{k}={str(v)},"
                tmp = tmp[:-1] + "]"
        msg = tmp
        is_str = True
    if len(msg) < SPLIT_SEND_MSG_LEN:
        return await rpc_send_group_msg(group_id, msg)
    md5 = get_md5(msg)
    msg = [msg[i:i+SPLIT_SEND_MSG_LEN] for i in range(0, len(msg), SPLIT_SEND_MSG_LEN)]
    set_footer_split_text(f"消息发送中...")
    await session.send_request('clear_group_msg_split', [])
    for i, m in enumerate(msg):
        set_footer_split_text(f"消息发送中... ({i+1}/{len(msg)})")
        ret = await session.send_request('upload_group_msg_split', [m, i])
        assert ret == i + 1
        log_box.add_line(f"发送分片消息: {i+1}/{len(msg)}")
    set_footer_split_text(f"消息发送中...")
    ret = await session.send_request('send_group_msg_split', [group_id, md5, is_str])
    clear_footer_split_text()
    return ret

class ServerDB:
    def __init__(self, name):
        self.name = name
        self.data = {}

    async def load(self):
        self.data = await rpc_get_client_data(self.name) or self.data

    def keys(self):
        return self.data.keys()

    def save(self):
        aloop.create_task(rpc_set_client_data(self.name, self.data))

    def get(self, key, default=None):
        return deepcopy(self.data.get(key, default))

    @require_connected
    def set(self, key, value):
        self.data[key] = deepcopy(value)
        self.save()

    @require_connected
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            self.save()

server_db = ServerDB(CLIENT_NAME)

async def reconnect():
    await session.close()

# ------------------------------------ 聊天逻辑 ------------------------------------ #

# 数据
@dataclass
class Data:
    # 当前延迟
    delay = 0
    # 显示比例
    chat_box_weight = 75
    # 群组数据列表
    group_list = None
    # 当前群组id
    cur_group_id = None
    # 当前群组名字
    cur_group_name = None
    # 当前群组
    cur_group = None
    # 当前群组消息列表
    msg_list = []
    # time_id计数器
    time_id_counter = {}
    # 用户别名
    alias = {}
    # 表情-url
    face_urls = []
    # 打开的群组
    opened_group = []
    # 未读消息数
    unread_msg = {}
    # 特别关注id
    watch_user_id = []
    # 是否有特别关注未读消息 
    has_watch_unread = {}
    # 自身id
    self_id = []
    # 是否有at和回复自己消息
    has_at_reply = {}
gdata = Data()

# 初始化file_db中的数据
def init_data_by_file_db():
    log_box.add_line("正在加载本地数据...")
    local_file_db.load()
    gdata.chat_box_weight = local_file_db.get('chat_box_weight', 75)
    set_chatbox_weight(gdata.chat_box_weight)
    log_box.add_line("加载本地数据成功")

# 初始化服务器中的数据
@require_connected
async def init_data_by_server():
    log_box.add_line("正在获取群组列表...")
    gdata.group_list = await rpc_get_group_list()
    log_box.add_line(f"获取到{len(gdata.group_list)}个群组")

    gdata.unread_msg.clear()
    gdata.has_watch_unread.clear()
    gdata.has_at_reply.clear()

    log_box.add_line("正在加载位于服务器的用户数据...")
    await server_db.load()
    gdata.cur_group_id   = server_db.get('cur_group_id', None)
    gdata.cur_group_name = server_db.get('cur_group_name', None)
    gdata.cur_group      = server_db.get('cur_group', None)
    gdata.alias          = server_db.get('alias', {})
    gdata.face_urls      = server_db.get('face_urls', [])
    gdata.opened_group   = server_db.get('opened_group', [])
    gdata.watch_user_id  = server_db.get('watch_user_id', [])
    gdata.self_id        = server_db.get('self_id', [])
    log_box.add_line("加载位于服务器的用户数据成功")

    log_box.add_line("正在更新群组信息...")
    
    new_opened_group = []
    for g in gdata.opened_group:
        new_g = None
        for g2 in gdata.group_list:
            if g2['group_id'] == g['group_id']:
                new_g = g2
                break
        if new_g:
            new_opened_group.append(new_g)
        else:
            log_box.add_line(f"群组 {g['group_name']} 已经不存在")
    gdata.opened_group = new_opened_group
    server_db.set('opened_group', gdata.opened_group)

    gdata.cur_group = None
    for g in gdata.group_list:
        if gdata.cur_group_id == g['group_id']:
            gdata.cur_group = g
            gdata.cur_group_name = g['group_name']
            break
    if gdata.cur_group is None:
        gdata.cur_group_id, gdata.cur_group_name = None, None
        log_box.add_line("当前群组不存在")
    server_db.set('cur_group_id', gdata.cur_group_id)
    server_db.set('cur_group_name', gdata.cur_group_name)
    server_db.set('cur_group', gdata.cur_group)

    log_box.add_line("更新群组信息成功")


    with open(os.path.join(this_file_path, 'server_db.json'), 'w', encoding='utf-8') as f:
        json.dump(server_db.data, f, indent=4, ensure_ascii=False)


# 根据msg_id查找消息
def find_msg_by_id(msg_id):
    for msg in gdata.msg_list:
        if str(msg['msg_id']) == str(msg_id):
            return msg
    return None

# 根据time_id查找消息
def find_msg_by_time_id(time_id):
    for msg in gdata.msg_list:
        if str(msg['time_id']) == str(time_id):
            return msg
    return None

# 根据qq号查询昵称
def find_nickname(user_id):
    for msg in reversed(gdata.msg_list):
        if str(msg['user_id']) == str(user_id):
            return msg['nickname']
    return user_id

# 获取faceurl项目中的图片key
def get_key_from_face_url_item(item):
    return item.get('key', None)

# 获取seg中的图片key
def get_key_from_image_seg(seg):
    return seg['data']['file_unique']

# 根据seg查找表情名
def find_face_by_seg(seg):
    for item in gdata.face_urls:
        if get_key_from_image_seg(seg) == get_key_from_face_url_item(item):
            return item['name']
    return None

# 根据表情名查找第一个url
def find_face_url_by_name(name):
    for face in gdata.face_urls:
        if face['name'] == name:
            return face['url']
    return None

# 根据表情名查找第一个表情
def find_face_by_name(name):
    for face in gdata.face_urls:
        if face['name'] == name:
            return face
    return None

# format图片消息
def format_image_msg(seg, msg_id, seg_ind, clickable=True):
    url = seg['data']['url']
    if url.startswith('https://gchat.qpic.cn/'):
        url = url.replace('https', 'http')
    is_face = 'subType' in seg['data'] and seg['data']['subType'] == 1

    if face_name := find_face_by_seg(seg):
        text = f"[{face_name}]"
    elif is_face:
        text = "[表情]"
    else:
        text = "[图片]"

    if clickable:
        return get_image_str(text, msg_id=msg_id, seg_ind=seg_ind)
    return text
    
# 获取第三方平台分享消息
def get_shared_msg(seg):
    try:
        data = seg['data']['data']
        data = json.loads(data)
        title = data["meta"]["detail_1"]["title"]
        desc = limit_str_len(data["meta"]["detail_1"]["desc"], 32)
        url = data["meta"]["detail_1"]["qqdocurl"]
        return get_hyperlink_str(f"[{title}:{desc}]", url)
    except:
        return None
    
# 获取json forward消息
def get_json_forward_msg(seg):
    try:
        data = seg['data']['data']
        data = json.loads(data)
        assert data['desc'] == "[聊天记录]" and data['prompt'] == "[聊天记录]"
        detail = data['meta']['detail']['news']
        msg = "[聊天记录]"
        for item in detail:
            msg += f"\n{item['text']}"
        return msg
    except:
        return None
    
# 获取群相册消息
def get_group_album_msg(seg):
    try:
        data = seg['data']['data']
        data = json.loads(data)
        assert '群相册' in data['prompt']
        return f"[{data['prompt']}]"
    except:
        return None

# 获取json消息
def get_json_msg(seg):
    if bmsg := get_shared_msg(seg):
        return bmsg
    if fmsg := get_json_forward_msg(seg):
        return fmsg
    if gamsg := get_group_album_msg(seg):
        return gamsg
    try:
        data = seg['data']['data']
        data = json.loads(data)
        text = f"[json消息: {data['prompt']}]"
        if url := find_key_recursive(data, 'jumpUrl'):
            return get_hyperlink_str(text, url)
        return text
    except:
        return "[json消息]"

# 获取消息dict
def get_msg_dict(msg):
    return {
        'msg_id': msg['message_id'],
        'time': msg['time'],
        'user_id': msg['sender']['user_id'],
        'nickname': msg['sender']['nickname'],
        'msg': msg['message'],
    }

# 打开真正的forward消息
async def open_forward_chat(seg):
    fid = seg['data']['id']
    # msgs = await rpc_get_forward_msg(fid)
    contents = seg['data']['content']
    msgs = [get_msg_dict(msg) for msg in contents]
    if not msgs:
        log_box.add_line(f"查看聊天记录失败: {fid}")
        return
    log_box.add_line(f"查看聊天记录: {fid}")
    forward_chat_box.clear_lines()
    forward_chat_box.add_line()
    forward_chat_box.add_line(f"-------- 聊天记录开始（共{len(msgs)}条） --------")
    for msg in msgs:
        await add_msg_to_chatbox(msg, forward_chat_box)
    forward_chat_box.add_line()
    forward_chat_box.add_line(f"-------- 聊天记录结束（共{len(msgs)}条） --------")
    forward_chat_box.set_focus_pos(0)
    show_forward_chat_box()

# 获取mface消息
def get_mface_msg(seg, use_link=True):
    text = "[QQ表情]"
    if 'summary' in seg['data']:
        text = seg['data']['summary']
    if 'url' in seg['data'] and use_link:
        text = get_image_str(text=text, link=seg['data']['url'])
    return text
    

# 获取消息中的文本内容
def get_msg_text(msg):
    text = ""
    for seg in msg["msg"]:
        if seg['type'] == 'text':
            text += seg['data']["text"]
    return text

# 在grouplist中查找群组
def find_group_by_id(group_id):
    for group in gdata.group_list:
        if group['group_id'] == group_id:
            return group
    return None

# 定位到某条消息
def locate_to_msg(msg):
    if 'focus_pos' not in msg:
        log_box.add_line(f"无法定位到消息: {msg}")
    chat_box.set_focus_pos(msg['focus_pos'])


# 展示群组列表
@require_connected
def show_group_list():
    if not gdata.group_list:
        log_box.add_line("群组列表为空")
        return
    log_box.add_line("群组列表:")
    for i, group in enumerate(gdata.group_list):
        log_box.add_line(f"{i+1}.{group['group_name']}({group['group_id']})")

# 添加消息到chatbox
@require_connected
async def add_msg_to_chatbox(msg, box: TextBox=chat_box):
    # 去除重复消息
    if box == chat_box and find_msg_by_id(msg['msg_id']): 
        return

    # 计算time_id
    if box == chat_box:
        time_str = datetime.fromtimestamp(int(msg['time'])).strftime('%H:%M:%S')
        if time_str not in gdata.time_id_counter:
            gdata.time_id_counter[time_str] = 0
            msg['time_id'] = f'{time_str}'
        else:
            gdata.time_id_counter[time_str] += 1
            msg['time_id'] = f'{time_str}.{gdata.time_id_counter[time_str]}'
    else:
        time_str = datetime.fromtimestamp(int(msg['time'])).strftime('%m-%d %H:%M:%S')
        msg['time_id'] = f'{time_str}'

    
    # 计算是否跨天
    if gdata.msg_list:
        pre_day = datetime.fromtimestamp(int(gdata.msg_list[-1]['time'])).day
        cur_day = datetime.fromtimestamp(int(msg['time'])).day
        if cur_day > pre_day:
            date_str = datetime.fromtimestamp(int(gdata.msg_list[-1]['time'])).strftime('%Y-%m-%d')
            box.add_line(f" ")
            box.add_line(f"-------- {date_str} messages end --------")

    # 用户别名
    user_id = str(msg['user_id'])
    alias = gdata.alias.get(user_id, None)
    
    # 第一行
    box.add_line()
    time_id_str = get_button_str(f"{msg['time_id']}", add_content_to_cmdline, [f">{msg['time_id']}", True])
    special_str = f"[❤]" if user_id in gdata.watch_user_id else ""
    alias_str = f"【{alias}】" if alias is not None else ""
    nickname_str = get_image_str(f"{msg['nickname']}", link=f"https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=100")
    admin_str = f"[A]" if int(msg['user_id'] in gdata.cur_group.get('admins', [])) else ""
    qid_str = get_button_str(f"{user_id}", add_content_to_cmdline, [f"@{user_id}", True])
    repeat_str = get_button_str(f"-", add_content_to_cmdline, [f"r[{msg['msg_id']}]", True])
    box.add_line(f"{time_id_str} {repeat_str} {alias_str}{nickname_str}({qid_str}){admin_str}{special_str}: ")
    msg['focus_pos'] = box.content_len() - 1

    # 内容
    content = ""
    for i, seg in enumerate(msg["msg"]):
        if seg['type'] == 'text':
            content += seg['data']["text"]

        elif seg['type'] == 'at':
            content += f"@{find_nickname(seg['data']['qq'])}"

        elif seg['type'] == 'image':
            content += format_image_msg(seg, msg['msg_id'], i, clickable=True)
            
        elif seg['type'] == 'face':
            content += f'[QQ表情]'

        elif seg['type'] == 'record':
            if 'url' not in seg['data']:
                content += f'[语音]'
            else:
                content += get_hyperlink_str("[语音]", seg['data']['url'])

        elif seg['type'] == 'json':
            content += get_json_msg(seg)

        elif seg['type'] == 'mface':
            content += get_mface_msg(seg)

        elif seg['type'] == 'video':
            content += f'[视频]'
        
        elif seg['type'] == 'forward':
            content += get_button_str("[聊天记录]", open_forward_chat, [seg])

        elif seg['type'] == 'file':
            content += f'[文件:{seg["data"].get("file")}]'

        elif seg['type'] == 'reply':
            if not (rmsg := find_msg_by_id(seg['data']['id'])):
                content += f'回复(未定位到消息)'
            else:
                reply_btn = get_button_str("回复", locate_to_msg, [rmsg])
                content += f'{reply_btn} \"{rmsg["nickname"]}({rmsg["user_id"]}): '
                rcontent = ''
                for seg in rmsg['msg']:
                    if seg['type'] == 'text':
                        rcontent += seg['data']['text']

                    elif seg['type'] == 'at':
                        rcontent += f"@{find_nickname(seg['data']['qq'])}"

                    elif seg['type'] == 'image':
                        rcontent += format_image_msg(seg, msg['msg_id'], i, clickable=False)

                    elif seg['type'] == 'face':
                        rcontent += f'[QQ表情]'

                    elif seg['type'] == 'reply':
                        rcontent += f'[回复]'

                    elif seg['type'] == 'json':
                        rcontent += f'[json消息]'
                    
                    elif seg['type'] == 'record':
                        rcontent += f'[语音]'

                    elif seg['type'] == 'mface':
                        rcontent += get_mface_msg(seg, use_link=False)
                    
                    elif seg['type'] == 'video':
                        rcontent += f'[视频]'

                    elif seg['type'] == 'forward':
                        rcontent += f'[聊天记录]'

                    elif seg['type'] == 'file':
                        rcontent += f'[文件]'

                if len(rcontent) > REPLY_MSG_LEN_LIMIT:
                    rcontent = rcontent[:REPLY_MSG_LEN_LIMIT] + '...'

                content += f'{rcontent}'
                content += '\"\n'
        else:
            content += f'[{str(seg)}]'

    box.add_line(content)

    if box == chat_box:
        gdata.msg_list.append(msg)

# 初始化群聊消息
@require_connected
async def init_group_msg():
    new_msg_count = gdata.unread_msg.get(str(gdata.cur_group_id), 0)
    chat_box.clear_lines()
    chat_box.add_line("消息加载中...")
    log_box.add_line("正在获取群组历史消息...")
    current_group = gdata.cur_group_id
    msgs = await rpc_get_group_history_msg(gdata.cur_group_id, MSG_HISTORY_LIMIT)
    if gdata.cur_group_id != current_group:
        log_box.add_line("群组已切换")
        return
    log_box.add_line(f"获取到{len(msgs)}条历史消息")
    chat_box.clear_lines()
    msgs.sort(key=lambda x: x['time'])
    for i, msg in enumerate(msgs):
        if i == len(msgs) - new_msg_count:
            chat_box.add_line()
            chat_box.add_line("-------- old messages --------")
        await add_msg_to_chatbox(msg)
    chat_box.set_focus_pos(chat_box.content_len() - 1)
    
# 获取新消息
@require_connected
async def get_group_new_msg():
    # 获取目前的群的新消息
    if gdata.cur_group_id:
        new_msgs = await rpc_get_group_new_msg(gdata.cur_group_id)
        # log_box.add_line(f"获取到新消息: {new_msgs}")
        new_msgs.sort(key=lambda x: x['time'])
        for i, msg in enumerate(new_msgs):
            await add_msg_to_chatbox(msg)

        # 更新目前群的未读消息数
        gdata.unread_msg[str(gdata.cur_group_id)] = 0
        gdata.has_watch_unread[str(gdata.cur_group_id)] = False
        gdata.has_at_reply[str(gdata.cur_group_id)] = False

    # 其他群的新消息
    for group in gdata.opened_group:
        if str(group['group_id']) != str(gdata.cur_group_id):
            new_msgs = await rpc_get_group_new_msg(group['group_id'])

            # 更新未读消息数
            if str(group['group_id']) not in gdata.unread_msg:
                gdata.unread_msg[str(group['group_id'])] = 0
            gdata.unread_msg[str(group['group_id'])] += len(new_msgs)

            for msg in new_msgs:
                # 检测是否有特别关注的未读消息
                if str(msg['user_id']) in gdata.watch_user_id:
                    gdata.has_watch_unread[str(group['group_id'])] = True
                # 检测是否有at和回复自己消息
                for seg in msg['msg']:
                    if seg['type'] == 'at' and str(seg['data']['qq']) in gdata.self_id:
                        gdata.has_at_reply[str(group['group_id'])] = True
                    if seg['type'] == 'reply':
                        reply_msg = find_msg_by_id(seg['data']['id'])
                        if reply_msg and reply_msg['user_id'] == gdata.self_id:
                            gdata.has_at_reply[str(group['group_id'])] = True

# 根据群id查找是否有已经打开的群，返回索引
def find_opened_group_index(group_id):
    for i, group in enumerate(gdata.opened_group):
        if str(group['group_id']) == str(group_id):
            return i
    return None

# 进入群组
@require_connected
async def enter_group(group):
    if not gdata.group_list:
        log_box.add_line("群组列表为空")
        return
    hide_server_log_box()
    hide_forward_chat_box()
    gdata.msg_list.clear()
    gdata.time_id_counter.clear()
    if group is None:
        log_box.add_line("刷新聊天")
    else:
        gdata.cur_group_id   = group['group_id']
        gdata.cur_group_name = group['group_name']
        gdata.cur_group      = group
        log_box.add_line(f"进入群组{gdata.cur_group_name}({gdata.cur_group_id})")   

        if find_opened_group_index(gdata.cur_group_id) is None:
            gdata.opened_group.append(gdata.cur_group)
            server_db.set('opened_group', gdata.opened_group)
            log_box.add_line(f"打开标签{gdata.cur_group_name}({gdata.cur_group_id})")

    server_db.set('cur_group_id', gdata.cur_group_id)
    server_db.set('cur_group_name', gdata.cur_group_name)
    server_db.set('cur_group', gdata.cur_group)
    await init_group_msg()

# 退出群组
async def exit_group():
    chat_box.clear_lines()
    log_box.add_line(f"退出群组{gdata.cur_group_name}({gdata.cur_group_id})")

    if (i := find_opened_group_index(gdata.cur_group_id)) is not None:
        gdata.opened_group.pop(i)
        server_db.set('opened_group', gdata.opened_group)
        log_box.add_line(f"关闭标签{gdata.cur_group_name}({gdata.cur_group_id})")

    gdata.cur_group_id = None
    gdata.cur_group_name = None
    gdata.cur_group = None
    
    server_db.set('cur_group_id', None)
    server_db.set('cur_group_name', None)
    server_db.set('cur_group', None)

    gdata.msg_list.clear()
    gdata.time_id_counter.clear()

# 添加别名
def add_alias(user_id, alias):
    if alias == 'None':
        gdata.alias.pop(user_id)
    else:
        gdata.alias[user_id] = alias
    server_db.set('alias', gdata.alias)
    log_box.add_line(f"添加别名: {user_id} -> {alias}")

# 添加表情
def add_face(time_id, name):
    msg = find_msg_by_time_id(time_id)
    for seg in msg['msg']:
        if seg['type'] == 'image':
            key = get_key_from_image_seg(seg)
            url = seg['data']['url']
            break
    gdata.face_urls.append({'name': name, 'url': url, 'key': key})
    server_db.set('face_urls', gdata.face_urls)
    log_box.add_line(f"添加表情: {key} -> {name}")

# 添加/删除特别关注
def switch_watch_user_id(user_id):
    if user_id not in gdata.watch_user_id:
        gdata.watch_user_id.append(str(user_id))
        server_db.set('watch_user_id', gdata.watch_user_id)
        log_box.add_line(f"添加特别关注: {user_id}")
    else:
        gdata.watch_user_id.remove(str(user_id))
        server_db.set('watch_user_id', gdata.watch_user_id)
        log_box.add_line(f"取消特别关注: {user_id}")

# 添加/删除自身id
def switch_self_id(user_id):
    if user_id not in gdata.self_id:
        gdata.self_id.append(str(user_id))
        server_db.set('self_id', gdata.self_id)
        log_box.add_line(f"添加自身id: {user_id}")
    else:
        gdata.self_id.remove(str(user_id))
        server_db.set('self_id', gdata.self_id)
        log_box.add_line(f"取消自身id: {user_id}")

# 发送消息
async def send_msg(msg):
    # 匹配纯文本
    msg = re.sub(r'raw\[(.*?)\]', r'\1', msg)
    # 匹配回复 
    reply_list = re.findall(r'>\d{2}:\d{2}:\d{2}\.\d{1}', msg) + re.findall(r'>\d{2}:\d{2}:\d{2}', msg)
    if reply_list and len(reply_list) == 1:
        time_id = reply_list[0][1:]
        if m := find_msg_by_time_id(time_id):
            msg = msg.replace(f'>{time_id}', f'[CQ:reply,id={m["msg_id"]}]')
        else:
            log_box.add_line(f'发送失败 无效的回复: {time_id}')
            return
    # 匹配at
    at_list = re.findall(r'@(\d+)', msg)
    for at in at_list:
        msg = msg.replace(f'@{at}', f'[CQ:at,qq={at}]')
    # 匹配图片 
    pic_list = re.findall(r'pic\[(.*?)\]', msg)
    for pic in pic_list:
        if not pic.startswith('http'):
            image_path = pic
            # 非GIF先缩小并保存为jpg
            if not pic.endswith('.gif'):
                image = Image.open(image_path).convert('RGB')
                image.thumbnail((1024, 1024))
                os.makedirs(TMP_FILE_FOLDER, exist_ok=True)
                tmp_save_path = os.path.join(TMP_FILE_FOLDER, 'image_to_send.jpg')
                image.save(tmp_save_path)
                image_path = tmp_save_path
            # 编码为base64发送
            with open(image_path, 'rb') as f:
                b64_image = f"base64://{base64.b64encode(f.read()).decode()}"
        msg = msg.replace(f'pic[{pic}]', f'[CQ:image,file={b64_image}]')
    # 匹配表情 
    face_list = re.findall(r'#(\w+)', msg)
    for face in face_list:
        if face_data := find_face_by_name(face):
            url = face_data['url']
            key = face_data['key']
            # 旧版QQ表情链接可以直接发送
            if url.startswith('https://gchat.qpic.cn/'):
                msg = msg.replace(f'#{face}', f'[CQ:image,file={face_data["url"]}]')
            else:
                # 如果有缓存的情况下使用缓存，没有的情况下按照url下载图片
                if image_path := get_local_image_path(key, url):
                    msg = msg.replace(f'#{face}', get_image_cq(image_path))
                else:
                    log_box.add_line(f'发送失败，无法下载表情: {face}')
                    return
        else:
            log_box.add_line(f'发送失败 无效的表情: {face}')
            return
    # 匹配复读
    repeat_list = re.findall(r'r\[-?\d+\]', msg)
    if repeat_list and len(repeat_list) == 1:
        id = repeat_list[0].replace('r[', '').replace(']', '')
        if m := find_msg_by_id(id):
            msg = m['msg']
            for seg in msg:
                # 如果是图片，有缓存的情况下使用缓存，没有的情况下下载图片再发送
                if seg['type'] == 'image':
                    key = get_key_from_image_seg(seg)
                    url = seg['data']['url']
                    if image_path := get_local_image_path(key, url):
                        with open(image_path, 'rb') as f:
                            b64_image = f"base64://{base64.b64encode(f.read()).decode()}"
                        seg['data'] = { "file": b64_image }
        else:
            log_box.add_line(f'发送失败 找不到消息: {id}')
            return
    if not msg: return
    msg = config.get('send_prefix', '') + msg + config.get('send_suffix', '')
    log_box.add_line(f"发送消息到{gdata.cur_group_name}({gdata.cur_group_id}): {limit_str_len(str(msg), 100)}")
    await rpc_send_group_msg_split(gdata.cur_group_id, msg)


# ------------------------------------ 更新逻辑 ------------------------------------ #

# 服务器连接循环
async def reconnect_loop():
    while True:
        async with PeriodControl(RECONNECT_INTERVAL):
            if not session.is_connected():
                log_box.add_line("正在连接服务器...")
                try:
                    set_footer_split_text("正在连接服务器...")
                    await session.connect()
                    log_box.add_line("连接成功")
                    set_footer_split_text("连接成功", 0.5)

                    await init_data_by_server()

                    if not gdata.cur_group_id or not find_group_by_id(gdata.cur_group_id):
                        log_box.add_line("历史群组不存在")

                        gdata.cur_group_id = None
                        gdata.cur_group_name = None
                        gdata.cur_group = None
                        server_db.set('cur_group_id', None)
                        server_db.set('cur_group_name', None)
                        server_db.set('cur_group', None)

                        if len(gdata.opened_group) > 0:
                            await enter_group(gdata.opened_group[0])
                        else:
                            show_group_list()
                    else:
                        await enter_group(None)

                    aloop.create_task(get_msg_loop())

                except Exception as exc:
                    set_footer_split_text("连接失败", 1)
                    log_box.add_line(f"连接失败: {exc}")
                    log_box.add_line(f"{traceback.format_exc()}")


# 消息获取循环
async def get_msg_loop():
    log_box.add_line("启动消息获取循环")
    while True:
        async with PeriodControl(MSG_GET_INTERVAL):
            # log_box.add_line(f"{gdata.cur_group_id} {session.is_connected()}")
            if session.is_connected():
                try:
                    await get_group_new_msg()
                except Exception as exc:
                    log_box.add_line(f"获取群组新消息失败: {exc}")
                    log_box.add_line(f"{traceback.format_exc()}")
            else:
                log_box.add_line("停止消息获取循环")
                break


# 命令处理循环
async def cmd_loop():
    while True: 
        try:
            cmd = await cmd_line.queue.get()
            if cmd is None: continue
            cmd = cmd.strip()
            if not cmd: continue

            if cmd in ["/exit", "/quit", "/q", "/close"]:
                if gdata.cur_group_id:
                    index = find_opened_group_index(gdata.cur_group_id)
                    next_group = gdata.opened_group[(index + 1) % len(gdata.opened_group)] if index is not None and len(gdata.opened_group) > 1 else None
                    await exit_group()
                    if next_group:
                        await enter_group(next_group)
                else:
                    if session.is_connected():
                        await session.close()
                    raise urwid.ExitMainLoop()
                
            elif cmd.startswith('/ratio '):
                chat_box_weight = int(cmd[7:])
                set_chatbox_weight(chat_box_weight, verbose=True)
        
            elif cmd == '/clear':
                log_box.clear_lines()

            elif cmd == '/reconnect':
                await reconnect()

            elif cmd.startswith('/echo '):
                log_box.add_line(cmd[6:])

            elif cmd == '/group':
                show_group_list()
            
            elif cmd == '/reload':
                await enter_group(None)

            elif cmd == '/rep':
                if not gdata.cur_group_id:
                    log_box.add_line("未进入群组")
                    return
                if not gdata.msg_list or len(gdata.msg_list) == 0:
                    log_box.add_line("消息列表为空")
                    return
                cmd_line.set_edit_text(get_msg_text(gdata.msg_list[-1]))
                cmd_line.set_edit_pos(len(cmd_line.get_edit_text()))

            elif cmd.startswith('/rep '):
                time_id = cmd[5:]
                if not gdata.cur_group_id:
                    log_box.add_line("未进入群组")
                    return
                if not gdata.msg_list or len(gdata.msg_list) == 0:
                    log_box.add_line("消息列表为空")
                    return
                if m := find_msg_by_time_id(time_id):
                    cmd_line.set_edit_text(get_msg_text(m))
                    cmd_line.set_edit_pos(len(cmd_line.get_edit_text()))
                else:
                    log_box.add_line(f'无效的time_id: {time_id}')

            elif cmd.startswith('/group '):
                index = int(cmd[7:]) - 1
                if index < 0 or index >= len(gdata.group_list):
                    log_box.add_line("无效的群组索引")
                    return
                await enter_group(gdata.group_list[index])

            elif cmd.startswith('/alias '):
                user_id, alias = cmd[7:].split(' ')
                add_alias(user_id, alias)
                await enter_group(None)
            
            elif cmd.startswith('/face '):
                time_id, name = cmd[6:].split(' ')
                add_face(time_id, name)
                await enter_group(None)

            elif cmd.startswith('/watch '):
                user_id = cmd[7:]
                switch_watch_user_id(user_id)
                await enter_group(None)

            elif cmd.startswith('/self '):
                user_id = cmd[6:]
                switch_self_id(user_id)
                await enter_group(None)

            elif cmd.startswith('/check '):
                time_id = cmd[7:].strip()
                if m := find_msg_by_time_id(time_id):
                    log_box.add_line(f'查找到消息: {m}')
                    unfold_logbox()
                else:
                    log_box.add_line(f'未找到消息: {time_id}')

            elif cmd == '/clientdata':
                data = await rpc_get_client_data(CLIENT_NAME)
                save_path = "client_data.json"
                log_box.add_line(f"获取客户端数据到{save_path}")
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)

            elif cmd.startswith('/'):
                set_footer_split_text(f"未知命令", 1)
                log_box.add_line(f"未知命令: {cmd}")

            else:
                await send_msg(cmd)

        except Exception as exc:
            if isinstance(exc, urwid.ExitMainLoop):
                raise exc
            log_box.add_line(f"命令处理失败: {exc}")
            log_box.add_line(f"{traceback.format_exc()}")
        

# ui更新循环
async def update_loop():
    # 菜单栏按钮
    reload_button = CustomButton("[刷新]", refresh)
    debug_button = CustomButton("[调试]", fold_or_unfold_logbox)
    log_button = CustomButton("[日志]", show_hide_server_log_box)
    exit_button = CustomButton("[退出]", exit_func)

    # 消息栏按钮
    down_button = CustomButton("[↓]", lambda: chat_box.set_bottom_focus())
    send_button = CustomButton("[双击发送]", double_click_to_send)
    clear_button = CustomButton("[清空文本]", lambda: cmd_line.clear())
    image_button = CustomButton("[发送图片]", select_image_file)
    face_button = CustomButton("[发送表情]", show_hide_face_bar)
    btn_bar.dividechars = 0
    btn_bar.contents = [
        (urwid.Text('---'), btn_bar.options(width_type='pack')),
        (down_button, btn_bar.options(width_type='pack')),
        (urwid.Text('--'), btn_bar.options(width_type='pack')),
        (send_button, btn_bar.options(width_type='pack')),
        (urwid.Text('--'), btn_bar.options(width_type='pack')),
        (face_button, btn_bar.options(width_type='pack')),
        (urwid.Text('--'), btn_bar.options(width_type='pack')),
        (image_button, btn_bar.options(width_type='pack')),
        (urwid.Text('--'), btn_bar.options(width_type='pack')),
        (clear_button, btn_bar.options(width_type='pack')),
        (urwid.Text('--------'), btn_bar.options(width_type='pack')),
    ]

    # 群按钮
    group_buttons = {}

    while True:
        async with PeriodControl(UPDATE_INTERVAL):
            # 更新标题
            title.dividechars = 1
            title.contents.clear()
            time_str = datetime.now().strftime('%m-%d %H:%M:%S')
            delay_str = f'{int(gdata.delay * 1000)}ms'
            online_str = f'(online:{delay_str})' if session.is_connected() else f'(offline)'
            group_str = ""
            group_index = find_opened_group_index(gdata.cur_group_id) 
            if group_index is None: group_index = -1
            if gdata.cur_group_id:
                group_str = f' [{group_index+1}/{len(gdata.opened_group)}] {gdata.cur_group_name}({gdata.cur_group_id})'
            title_text = urwid.Text(f'[{time_str}] LUNABOT客户端')

            title.contents.append((title_text, title.options(width_type='pack')))
            title.contents.append((reload_button, title.options(width_type='pack')))
            title.contents.append((debug_button, title.options(width_type='pack')))
            title.contents.append((log_button, title.options(width_type='pack')))
            title.contents.append((exit_button, title.options(width_type='pack')))

            title.contents.append((urwid.Text(f"{online_str}"), title.options(width_type='pack')))

            # 更新tab_bar
            line_group_num = 7
            if len(tab_bars.contents) == 0:
                for i in range((len(gdata.opened_group) + line_group_num - 1) // line_group_num):
                    tab_bars.contents.append((urwid.Columns([]), tab_bars.options()))

            cur_group = 0
            for item in tab_bars.contents:
                tab_bar: urwid.Columns = item[0]
                tab_bar.dividechars = 2
                tab_bar.contents.clear()
                for i, group in enumerate(gdata.opened_group[cur_group:cur_group+line_group_num]):
                    button_text = ""
                    if group['group_id'] == gdata.cur_group_id:
                        name = limit_str_len(group['group_name'], 32)
                        button_text = f'【{name}】'
                    else:
                        name = limit_str_len(group['group_name'], 8)
                        unread_count = gdata.unread_msg.get(str(group['group_id']), 0)
                        unread_str = ''
                        if unread_count > 99:
                            unread_str = '(99+)'
                        elif unread_count > 0:
                            unread_str = f"({unread_count})"
                        if gdata.has_at_reply.get(str(group['group_id']), False):
                            unread_str = unread_str.replace(')', '@)')
                        if gdata.has_watch_unread.get(str(group['group_id']), False):
                            unread_str = unread_str.replace(')', '!)')
                        button_text += f'{name}{unread_str}'

                    if group['group_id'] in group_buttons:
                        button = group_buttons[group['group_id']]
                        button.set_label(button_text)
                    else:
                        button = CustomButton(button_text, enter_group, [group])
                        group_buttons[group['group_id']] = button

                    tab_bar.contents.append((button, tab_bar.options(width_type='pack')))
                cur_group += line_group_num

            # 更新表情
            face_bar_lines = [urwid.Text('---------------------- 表情列表 ----------------------')]
            faces, face_set = [], set()
            for face in gdata.face_urls:
                if face['name'] not in face_set:
                    faces.append(face)
                    face_set.add(face['name'])
            if faces:
                LINE_FACE_COUNT = 8
                for i in range(0, len(faces), LINE_FACE_COUNT):
                    line = urwid.Columns([])
                    line.dividechars = 1
                    for face in faces[i:i+LINE_FACE_COUNT]:
                        button = CustomButton(f"[{face['name']}]", double_click_face_to_send, [face["name"]])
                        line.contents.append((button, line.options(width_type='pack')))
                    face_bar_lines.append(line)
            global face_bar
            face_bar = urwid.Pile(face_bar_lines)

            loop.draw_screen()


# heartbeat循环
async def heartbeat_loop():
    fail_count = 0
    while True:
        async with PeriodControl(HEARTBEAT_INTERVAL):
            if session.is_connected():
                try:
                    t = datetime.now().timestamp()
                    res = await session.send_request('echo', [str(t)])
                    assert res.split(' ')[1] == str(t)
                    gdata.delay = datetime.now().timestamp() - t
                    fail_count = 0
                except Exception as exc:
                    fail_count += 1
                    log_box.add_line(f"心跳失败({fail_count}/{HEARTBEAT_FAIL_LIMIT}): {exc}")
                    log_box.add_line(f"{traceback.format_exc()}")
                    if fail_count >= HEARTBEAT_FAIL_LIMIT:
                        log_box.add_line(f"心跳失败次数过多，断开连接")
                        await session.close()
                        fail_count = 0


# 按键处理
def global_keypress(key):
    if key == 'ctrl c':
        log_box.add_line("退出")
        raise urwid.ExitMainLoop()
    elif key == 'ctrl e':
        gdata.chat_box_weight -= 5
        set_chatbox_weight(gdata.chat_box_weight)
        
    elif key == 'ctrl r':
        gdata.chat_box_weight += 5
        set_chatbox_weight(gdata.chat_box_weight)
    elif key == 'ctrl n':
        if not gdata.group_list or not session.is_connected(): return
        if len(gdata.opened_group) == 0: return
        index = find_opened_group_index(gdata.cur_group_id)
        if index is None: return
        index = (index + 1) % len(gdata.opened_group)
        aloop.create_task(enter_group(gdata.opened_group[index]))
    

# ------------------------------------ 主程序 ------------------------------------ #

if __name__ == '__main__':
    try:
        init_data_by_file_db()
        aloop = asyncio.get_event_loop()
        ev_loop = urwid.AsyncioEventLoop(loop=aloop)
        loop = urwid.MainLoop(layout, PALLETE, unhandled_input=global_keypress, event_loop=ev_loop)
        aloop.create_task(reconnect_loop())
        aloop.create_task(cmd_loop())
        aloop.create_task(update_loop())
        aloop.create_task(heartbeat_loop())
        loop.run()
    except KeyboardInterrupt:
        pass
    except urwid.ExitMainLoop:
        pass
    print('\033[H\033[J', end='')
