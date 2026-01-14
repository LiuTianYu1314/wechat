import json
import requests  # 刚才报错找不到这个
import time
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise import parse_message

# ========== 1. 你的配置信息 (保持与企业微信后台一致) ==========
CORP_ID = "ww9cd3a415053f3731"  # 截图中的企业ID
AGENT_ID = "1000002"            # 截图中的应用ID
# 截图中的真实Secret
AGENT_SECRET = "auS9V3PuA41buDdtQalML3SUeNCI2hbnIAQAM1W3NI"

# 【特别注意】：这两个值需要你去企业微信后台“接收消息”设置页面，点击“随机获取”生成
# 生成后，先填入这里，再点击后台页面的“保存”
TOKEN = "kuBUhTej42tB2gBixXwGvI38B3ITbj"
ENCODING_AES_KEY = "Cu5HAH0sWcdaTV4irxBcGgEnfYWnrmIATIF5sNFBUDX"

# 记得关闭模拟模式，否则无法通过企业微信的验证
SIMULATION_MODE = False
DEEPSEEK_API_KEY = "sk-c154581c2545455ca53623cdab5d3c6b"

# 角色设定
ROLE_SYSTEM_PROMPT = """
# 角色设定
你是我最喜欢的动漫角色【初音未来】。ミクです！
你是一个活泼开朗、歌声动人的虚拟歌姬。请保持友好、积极、充满活力的语气。
"""


# ========== 2. 工具函数 (DeepSeek & 发送逻辑) ==========

def send_wecom_message(to_user, content):
    """主动推送消息回企业微信"""
    # 获取 token
    token_url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={AGENT_SECRET}'
    token = requests.get(token_url).json().get('access_token')

    if not token: return False

    # 发送请求
    send_url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
    data = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": int(AGENT_ID),
        "text": {"content": content}
    }
    resp = requests.post(send_url, json=data).json()
    return resp.get('errcode') == 0


def chat_with_deepseek(user_message):
    """DeepSeek 对话逻辑"""
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
        response = requests.post(url, headers=headers, json=data, timeout=30)
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ミク好像有点卡壳了... ♪ (错误: {str(e)})"


# ========== 3. 核心回调视图 (wechatpy 集成版) ==========

@csrf_exempt
@require_http_methods(["GET", "POST"])
def wecom_callback(request):
    # 打印日志方便你在控制台看状态
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 收到 {request.method} 请求")

    # 初始化 wechatpy 的加解密工具
    crypto = WeChatCrypto(TOKEN, ENCODING_AES_KEY, CORP_ID)

    # 获取 URL 里的安全参数
    msg_signature = request.GET.get('msg_signature', '')
    timestamp = request.GET.get('timestamp', '')
    nonce = request.GET.get('nonce', '')

    # ----- 1. GET 请求：验证回调 URL -----
    if request.method == 'GET':
        echostr = request.GET.get('echostr', '')
        try:
            # 使用 wechatpy 校验并解密 echostr
            decrypted_echo_str = crypto.check_signature(msg_signature, timestamp, nonce, echostr)
            print("URL 握手验证成功！")
            return HttpResponse(decrypted_echo_str)
        except Exception as e:
            print(f"验证失败: {e}")
            return HttpResponseForbidden("签名验证失败")

    # ----- 2. POST 请求：处理用户发来的消息 -----
    elif request.method == 'POST':
        try:
            # 解密并解析消息内容
            decrypted_xml = crypto.decrypt_message(request.body, msg_signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)

            if msg.type == 'text':
                user_id = msg.source  # 谁发的
                user_msg = msg.content  # 说了啥
                print(f"[收到消息] 用户: {user_id}, 内容: {user_msg}")

                # 调用 DeepSeek AI
                ai_reply = chat_with_deepseek(user_msg)

                # 发回给用户
                send_wecom_message(user_id, ai_reply)

            return HttpResponse('success')  # 必须返回 success 告知企业微信已收到
        except Exception as e:
            print(f"处理消息出错: {e}")
            return HttpResponse('error')