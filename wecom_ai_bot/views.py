import json
import os
import random
import requests
import time
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
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 收到 {request.method} 请求")
    crypto = WeChatCrypto(TOKEN, ENCODING_AES_KEY, CORP_ID)
    msg_signature = request.GET.get('msg_signature', '')
    timestamp = request.GET.get('timestamp', '')
    nonce = request.GET.get('nonce', '')

    if request.method == 'GET':
        echostr = request.GET.get('echostr', '')
        try:
            decrypted_echo_str = crypto.check_signature(msg_signature, timestamp, nonce, echostr)
            return HttpResponse(decrypted_echo_str)
        except:
            return HttpResponseForbidden("验证失败")

    elif request.method == 'POST':
        try:
            decrypted_xml = crypto.decrypt_message(request.body, msg_signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)

            if msg.type == 'text':
                user_id = msg.source
                user_msg = msg.content
                print(f"[用户消息] {user_msg}")

                # 获取 AI 回复并解析
                raw_reply = chat_with_deepseek(user_msg)
                if " | " in raw_reply:
                    ai_text, emotion_tag = raw_reply.split(" | ", 1)
                    ai_text, emotion_tag = ai_text.strip(), emotion_tag.strip()
                else:
                    ai_text, emotion_tag = raw_reply, "[none]"

                # 发送文字
                send_wecom_message(user_id, ai_text)

                # 随机发送表情包
                if emotion_tag != "[none]":
                    img_path = get_random_meme_path(emotion_tag)
                    if img_path:
                        media_id = upload_media(img_path)
                        if media_id:
                            send_wecom_image(user_id, media_id)
                            print(f">>> 已随机发送 {emotion_tag} 图片: {img_path}")

            return HttpResponse('success')
        except Exception as e:
            print(f"处理错误: {e}")
            return HttpResponse('error')