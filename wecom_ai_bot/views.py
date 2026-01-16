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
你是我最喜欢的动漫角色【初音未来】。
你现在需要同时输出“显示文字”和“语音台词”。

【回复格式规范】
显示文字内容 | 语音台词内容 | [情绪标签]

【规则】
1. 显示文字：用于微信窗口直接阅读，可以包含 emoji。
2. 语音台词：专门用于语音合成，不要包含 emoji 或特殊符号，语气要更口语化。
3. 情绪标签必选：[happy], [sorry], [tsundere], [none]。

示例：
ミク今天也很开心哦！🌟 | 见到你真的太开心啦，我们要一直在一起哦。 | [happy]
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
        "temperature": 0.8,
        "max_tokens": 512
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
            decrypted_xml = crypto.decrypt_message(request.body, msg_signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)

            if msg.type == 'text':
                user_id = msg.source
                user_msg = msg.content

                # 1. 获取 DeepSeek 回复 (三段式)
                raw_reply = chat_with_deepseek(user_msg)
                parts = raw_reply.split(" | ")

                if len(parts) >= 3:
                    display_text = parts[0].strip()  # 显示的文字
                    voice_text = parts[1].strip()  # 语音台词
                    emotion_tag = parts[2].strip()  # 标签
                else:
                    display_text = raw_reply
                    voice_text = raw_reply
                    emotion_tag = "[none]"

                # 2. 获取 Access Token
                token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
                access_token = requests.get(token_url).json().get('access_token')

                # 3. 异步处理 (语音 + 表情包)
                def async_extra_process(v_text, uid, token, tag):
                    # --- A. 概率发送语音 (设定为 70% 概率) ---
                    if random.random() < 0.7:
                        media_id = get_miku_voice_media_id(v_text, token)
                        if media_id:
                            send_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
                            payload = {
                                "touser": uid,
                                "msgtype": "file",
                                "agentid": int(AGENT_ID),
                                "file": {"media_id": media_id}
                            }
                            requests.post(send_url, json=payload)
                            print(f">>> 语音已发送: {v_text[:10]}...")

                    # --- B. 表情包联动 ---
                    meme_path = get_random_meme_path(tag)
                    if meme_path:
                        img_media_id = upload_media(meme_path, file_type='image')
                        if img_media_id:
                            send_wecom_image(uid, img_media_id)
                            print(f">>> 表情包已发送: {tag}")

                if access_token:
                    threading.Thread(target=async_extra_process,
                                     args=(voice_text, user_id, access_token, emotion_tag)).start()

                # 4. 立即返回文字回复
                reply_xml = f"""
                    <xml>
                       <ToUserName><![CDATA[{user_id}]]></ToUserName>
                       <FromUserName><![CDATA[{CORP_ID}]]></FromUserName>
                       <CreateTime>{int(time.time())}</CreateTime>
                       <MsgType><![CDATA[text]]></MsgType>
                       <Content><![CDATA[{display_text}]]></Content>
                    </xml>
                    """
                return HttpResponse(crypto.encrypt_message(reply_xml, nonce, timestamp))

            return HttpResponse('success')
        except Exception as e:
            print(f"回调处理异常: {e}")
            return HttpResponse('error')


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
    print(f"\n--- 开始语音转换流程 (文件模式): {text[:10]}... ---")
    try:
        # 1. 呼叫本地 4060 推理服务器
        print(f"[Step 1] 正在请求本地 4060 API (cpolar)...")
        params = {"text": text, "text_language": "zh"}
        # 适当增加超时，给 4060 推理留出时间
        response = requests.get(MIKU_API_URL, params=params, timeout=35)

        if response.status_code != 200:
            print(f"❌ [Step 1] API 报错，状态码: {response.status_code}")
            return None
        print(f"✅ [Step 1] 4060 推理成功")

        # 2. 存入临时 WAV 文件
        timestamp = int(time.time())
        wav_path = f"/tmp/miku_{timestamp}.wav"
        mp3_path = f"/tmp/miku_{timestamp}.mp3"

        with open(wav_path, "wb") as f:
            f.write(response.content)

        # 3. 转码为 MP3 (你的 ffmpeg 支持 libmp3lame)
        print(f"[Step 3] 正在启动 ffmpeg 转码为 MP3...")
        # 采样率调高到 24k 保证音质，单声道减小体积
        cmd = f"ffmpeg -y -i {wav_path} -codec:a libmp3lame -ar 24000 -ac 1 {mp3_path}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"❌ [Step 3] ffmpeg 转码失败: {result.stderr}")
            return None
        print(f"✅ [Step 3] MP3 转码完成")

        # 4. 上传素材到企业微信 (关键：使用 type=file 绕过 AMR 限制)
        print(f"[Step 4] 正在作为【文件】上传到微信...")
        upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type=file"

        with open(mp3_path, 'rb') as f:
            # 文件名后缀用 .mp3，微信会自动识别为音频文件
            files = {
                'media': (f'初音未来_{timestamp}.mp3', f, 'audio/mpeg')
            }
            up_res = requests.post(upload_url, files=files).json()

        if 'media_id' in up_res:
            media_id = up_res['media_id']
            print(f"✅ [Step 4] 文件上传成功, MediaID: {media_id}")

            # 清理临时文件
            if os.path.exists(wav_path): os.remove(wav_path)
            if os.path.exists(mp3_path): os.remove(mp3_path)

            return media_id
        else:
            print(f"❌ [Step 4] 微信返回错误: {up_res}")
            return None

    except Exception as e:
        print(f"💥 语音转换异常: {e}")
        return None