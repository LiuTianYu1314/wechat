import json
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
AGENT_SECRET = "auS9V3PuA41buDdtQalML3SUeNCI2hbnIAQAM1W3NI"

# 企业微信后台“接收消息”设置页面生成的 Token 和 AESKey
TOKEN = "kuBUhTej42tB2gBixXwGvI38B3ITbj"
ENCODING_AES_KEY = "Cu5HAH0sWcdaTV4irxBcGgEnfYWnrmIATIF5sNFBUDX"

# 服务器验证开关
SIMULATION_MODE = False
# 你的 DeepSeek API KEY
DEEPSEEK_API_KEY = "sk-c154581c2545455ca53623cdab5d3c6b"

# 角色设定
ROLE_SYSTEM_PROMPT = """
# 角色设定
你是我最喜欢的动漫角色【初音未来】。ミクです！
你是一个活泼开朗、歌声动人的虚拟歌姬。请保持友好、积极、充满活力的语气。回复尽量简洁，多用语气词。
"""


# ========== 2. 工具函数 (DeepSeek & 消息发送) ==========

def send_wecom_message(to_user, content):
    """主动推送消息回企业微信用户"""
    # 1. 获取 access_token
    token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
    try:
        token_resp = requests.get(token_url).json()
        token = token_resp.get('access_token')
        if not token:
            print(f"获取 Token 失败: {token_resp}")
            return False
    except Exception as e:
        print(f"Token 请求异常: {e}")
        return False

    # 2. 发送消息
    send_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
    data = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": int(AGENT_ID),
        "text": {"content": content}
    }
    try:
        resp = requests.post(send_url, json=data).json()
        print(f"微信回传结果: {resp}")
        return resp.get('errcode') == 0
    except Exception as e:
        print(f"消息回传异常: {e}")
        return False


def chat_with_deepseek(user_message):
    """请求 DeepSeek API 获取 AI 回复"""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
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
        ai_content = response.json()["choices"][0]["message"]["content"]
        print(f"DeepSeek 成功响应: {ai_content[:20]}...")
        return ai_content
    except Exception as e:
        error_msg = f"ミク好像有点卡壳了... ♪ (错误: {str(e)})"
        print(error_msg)
        return error_msg


# ========== 3. 核心回调视图 (处理企业微信请求) ==========

@csrf_exempt
@require_http_methods(["GET", "POST"])
def wecom_callback(request):
    # 初始化日志记录请求时间
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 收到 {request.method} 请求")

    # 初始化加解密工具
    crypto = WeChatCrypto(TOKEN, ENCODING_AES_KEY, CORP_ID)

    # 获取安全参数
    msg_signature = request.GET.get('msg_signature', '')
    timestamp = request.GET.get('timestamp', '')
    nonce = request.GET.get('nonce', '')

    # ----- 场景 A: GET 请求 (企业微信后台验证 URL 握手) -----
    if request.method == 'GET':
        echostr = request.GET.get('echostr', '')
        try:
            decrypted_echo_str = crypto.check_signature(msg_signature, timestamp, nonce, echostr)
            print(">>> URL 握手验证成功！")
            return HttpResponse(decrypted_echo_str)
        except Exception as e:
            print(f">>> 验证失败: {e}")
            return HttpResponseForbidden("签名验证失败")

    # ----- 场景 B: POST 请求 (处理用户消息) -----
    elif request.method == 'POST':
        try:
            # 1. 解密消息
            decrypted_xml = crypto.decrypt_message(request.body, msg_signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)

            # 2. 如果是文字消息
            if msg.type == 'text':
                user_id = msg.source
                user_msg = msg.content
                print(f"[收到消息] 用户: {user_id}, 内容: {user_msg}")

                # 3. 调用 AI 逻辑
                ai_reply = chat_with_deepseek(user_msg)

                # 4. 回复消息给用户
                send_wecom_message(user_id, ai_reply)

            return HttpResponse('success')
        except Exception as e:
            print(f"[消息处理异常]: {e}")
            return HttpResponse('error')