import json
import os
import random
import requests
import time
import subprocess
import threading
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise import parse_message

# ========== 1. 你的配置信息 (确保与企业微信后台完全一致) ==========
CORP_ID = "ww9cd3a415053f3731"
AGENT_ID = "1000002"
AGENT_SECRET = "auS9V3PuA41buDdtQalML3SUeNCI2hbnIAQAMl1W3NI"

# 企业微信后台“接收消息”设置页面生成的 Token 和 AESKey
TOKEN = "kuBUhTej42tB2gBixXwGvI38B3ITbj"
ENCODING_AES_KEY = "Cu5HAH0sWcdaTV4irxBcGgEnfYWnrmIATIF5sNFBUDX"
DEEPSEEK_API_KEY = "sk-c154581c2545455ca53623cdab5d3c6b"

# 服务器验证开关
SIMULATION_MODE = False

# 角色设定
ROLE_SYSTEM_PROMPT = """
你是我最喜欢的动漫角色【初音未来】。ミクです！
你是一个活泼开朗、歌声动人的虚拟歌姬。

【重要回复格式】
你的每一条回复必须按照以下格式：
回复内容 | [情绪标签]

可选的情绪标签如下：
- [happy]：当你感到开心、欢迎、微笑或心情好时使用。
- [sorry]：当你道歉、感到遗憾、难过或委屈时使用。
- [tsundere]：当你表现傲娇、害羞、生气或不想理人时使用。
- [none]：如果不符合以上任何情绪，请使用这个。

示例：
ミク今天也很开心哦！ | [happy]
"""

# ========== 2. 工具函数 (DeepSeek & 消息发送 & 素材上传) ==========

def send_wecom_message(to_user, content):
    """主动推送文字消息"""
    token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
    try:
        token = requests.get(token_url).json().get('access_token')
        if not token: return False

        send_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
        data = {
            "touser": to_user,
            "msgtype": "text",
            "agentid": int(AGENT_ID),
            "text": {"content": content}
        }
        resp = requests.post(send_url, json=data).json()
        return resp.get('errcode') == 0
    except Exception as e:
        print(f"文字发送异常: {e}")
        return False


def upload_media(file_path, file_type='image'):
    """上传本地文件到微信临时素材库，获取 media_id"""
    token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
    try:
        token = requests.get(token_url).json().get('access_token')
        upload_url = f'https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type={file_type}'
        with open(file_path, 'rb') as f:
            resp = requests.post(upload_url, files={'media': f}).json()
            if resp.get('errcode') == 0:
                return resp.get('media_id')
            print(f"素材上传失败: {resp}")
    except Exception as e:
        print(f"素材上传异常: {e}")
    return None


def send_wecom_image(to_user, media_id):
    """通过 media_id 推送图片消息"""
    token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
    try:
        token = requests.get(token_url).json().get('access_token')
        send_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
        data = {
            "touser": to_user,
            "msgtype": "image",
            "agentid": int(AGENT_ID),
            "image": {"media_id": media_id}
        }
        requests.post(send_url, json=data)
    except Exception as e:
        print(f"图片发送异常: {e}")


def chat_with_deepseek(user_message):
    """请求 DeepSeek API 获取带标签的回复"""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": ROLE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7
    }
    try:
        print(f"正在呼叫 DeepSeek... 询问: {user_message}")
        response = requests.post(url, headers=headers, json=data, timeout=30)
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ミク好像有点卡壳了... ♪ | [none]"


def get_random_meme_path(emotion_tag):
    """根据标签从 /opt/wechat/meme/ 随机选图"""
    mapping = {"[happy]": "happy", "[sorry]": "sorry", "[tsundere]": "tsundere"}
    folder_name = mapping.get(emotion_tag)
    if not folder_name: return None

    base_dir = f"/opt/wechat/meme/{folder_name}"
    image_files = ["1.jpg", "2.jpg", "3.jpg"]
    selected_file = random.choice(image_files)
    full_path = os.path.join(base_dir, selected_file)

    return full_path if os.path.exists(full_path) else None


# ========== 3. 核心回调视图 (入口) ==========

@csrf_exempt
@require_http_methods(["GET", "POST"])
def wecom_callback(request):
    crypto = WeChatCrypto(TOKEN, ENCODING_AES_KEY, CORP_ID)
    msg_signature = request.GET.get('msg_signature', '')
    timestamp = request.GET.get('timestamp', '')
    nonce = request.GET.get('nonce', '')

    if request.method == 'POST':
        try:
            # 解密消息
            decrypted_xml = crypto.decrypt_message(request.body, msg_signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)

            if msg.type == 'text':
                user_id = msg.source
                user_msg = msg.content

                # 1. 获取 DeepSeek 回复内容
                raw_reply = chat_with_deepseek(user_msg)
                if " | " in raw_reply:
                    ai_text, emotion_tag = raw_reply.split(" | ", 1)
                    ai_text = ai_text.strip()
                else:
                    ai_text = raw_reply

                # 2. 获取 Access Token 用于后续主动推送
                token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
                token_res = requests.get(token_url).json()
                access_token = token_res.get('access_token')

                # 3. 关键：定义一个内部函数用于后台异步处理语音
                def async_voice_process(text, uid, token):
                    # 调用语音合成和上传逻辑
                    media_id = get_miku_voice_media_id(text, token)
                    if media_id:
                        # 使用主动消息发送接口推送语音
                        send_voice_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
                        voice_data = {
                            "touser": uid,
                            "msgtype": "voice",
                            "agentid": int(AGENT_ID),
                            "voice": {"media_id": media_id}
                        }
                        requests.post(send_voice_url, json=voice_data)
                        print(f">>> 异步语音推送成功: {text[:10]}...")

                # 4. 启动后台线程，不阻塞当前响应
                if access_token:
                    threading.Thread(target=async_voice_process, args=(ai_text, user_id, access_token)).start()

                # 5. 立即构建并返回文字回复的加密 XML
                reply_xml = f"""
                <xml>
                   <ToUserName><![CDATA[{user_id}]]></ToUserName>
                   <FromUserName><![CDATA[{CORP_ID}]]></FromUserName>
                   <CreateTime>{int(time.time())}</CreateTime>
                   <MsgType><![CDATA[text]]></MsgType>
                   <Content><![CDATA[{ai_text}]]></Content>
                </xml>
                """
                return HttpResponse(crypto.encrypt_message(reply_xml, nonce, timestamp))

            return HttpResponse('success')
        except Exception as e:
            print(f"回调处理异常: {e}")
            return HttpResponse('error')

    # GET 请求用于企业微信验证 URL
    echostr = request.GET.get('echostr', '')
    try:
        return HttpResponse(crypto.check_signature(msg_signature, timestamp, nonce, echostr))
    except Exception:
        return HttpResponseForbidden()


def handle_wechat_voice(text, media_id_func):
    """
    text: DeepSeek 返回的文本内容
    media_id_func: 你之前写的上传临时素材获取 MediaID 的函数
    """
    # --- 配置 ---
    LOCAL_API_URL = "http://你的cpolar随机地址.cpolar.io"
    WAV_TEMP = f"/tmp/miku_{int(time.time())}.wav"
    AMR_TEMP = WAV_TEMP.replace(".wav", ".amr")

    try:
        # 1. 请求本地 4060 进行合成
        res = requests.get(LOCAL_API_URL, params={
            "text": text,
            "text_language": "zh"
        }, timeout=60)

        if res.status_code == 200:
            # 2. 保存原始音频
            with open(WAV_TEMP, "wb") as f:
                f.write(res.content)

            # 3. 转码为企业微信必须的 AMR 格式 (8000Hz, 单声道)
            # 使用 subprocess 调用 ffmpeg
            cmd = f"ffmpeg -y -i {WAV_TEMP} -ar 8000 -ab 12.2k -ac 1 {AMR_TEMP}"
            subprocess.run(cmd, shell=True, check=True)

            # 4. 上传到企业微信获取 Media_ID
            media_id = media_id_func(AMR_TEMP)

            # 5. 清理临时文件
            os.remove(WAV_TEMP)
            os.remove(AMR_TEMP)

            return media_id
    except Exception as e:
        print(f"语音合成或转码失败: {e}")
    return None


# 你的 cpolar 公网地址
MIKU_API_URL = "http://67adaaae.r2.cpolar.top"


def get_miku_voice_media_id(text, access_token):
    try:
        # 1. 增加超时时间到 30 秒，确保长句子推理不中断
        response = requests.get(MIKU_API_URL, params={"text": text, "text_language": "zh"}, timeout=30)

        if response.status_code != 200 or not response.content:
            print(f"API 请求失败或内容为空: {response.status_code}")
            return None

        timestamp = int(time.time())
        wav_file = f"/tmp/miku_{timestamp}.wav"
        amr_file = f"/tmp/miku_{timestamp}.amr"

        # 2. 写入文件并强制刷新到磁盘
        with open(wav_file, "wb") as f:
            f.write(response.content)
            f.flush()
            os.fsync(f.fileno())

        # 3. 检查文件是否真的存在且有内容
        if not os.path.exists(wav_file) or os.path.getsize(wav_file) == 0:
            print("WAV 文件生成失败，ffmpeg 无法处理")
            return None

        # 4. 转码 (增加 -y 覆盖参数)
        cmd = f"ffmpeg -y -i {wav_file} -ar 8000 -ab 12.2k -ac 1 {amr_file}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"FFmpeg 报错日志: {result.stderr}")
            return None

        # 5. 上传至微信
        upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type=voice"
        with open(amr_file, 'rb') as f:
            upload_res = requests.post(upload_url, files={'media': f}).json()

        # 清理
        for f in [wav_file, amr_file]:
            if os.path.exists(f): os.remove(f)

        return upload_res.get("media_id")
    except Exception as e:
        print(f"语音流程异常: {e}")
        return None