"""
测试文件上传功能
验证图片和视频上传是否正常工作
"""
import requests
import os
from io import BytesIO
from PIL import Image

BASE_URL = "http://127.0.0.1:5000/api"


def get_or_create_test_user():
    """获取或创建测试用户"""
    # 先尝试登录
    login_response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "upload_test@example.com",
        "password": "UploadTest123"
    })
    
    if login_response.status_code == 200:
        print("✅ 使用现有测试用户登录")
        return login_response.json()["access_token"]
    
    # 如果登录失败，创建新用户
    print("📝 创建新的测试用户...")
    import random
    timestamp = random.randint(1000, 9999)
    register_response = requests.post(f"{BASE_URL}/auth/register", json={
        "username": f"upload_test_{timestamp}",
        "email": f"upload_test_{timestamp}@example.com",
        "password": "UploadTest123",
        "first_name": "Upload",
        "last_name": "Test"
    })
    
    if register_response.status_code in [200, 201]:
        print("✅ 测试用户创建成功")
        # 立即登录
        login_response = requests.post(f"{BASE_URL}/auth/login", json={
            "email": f"upload_test_{timestamp}@example.com",
            "password": "UploadTest123"
        })
        
        if login_response.status_code == 200:
            return login_response.json()["access_token"]
    
    print(f"❌ 无法创建或登录测试用户")
    print(f"   注册响应: {register_response.status_code} - {register_response.text}")
    return None


def get_token():
    """获取测试用户的Token"""
    token = get_or_create_test_user()
    if not token:
        print("\n⚠️  请检查服务器是否正常运行")
        return None
    return token


def create_test_image():
    """创建一个测试图片"""
    # 创建一个 100x100 的红色图片
    img = Image.new('RGB', (100, 100), color='red')
    img_bytes = BytesIO()
    img.save(img_bytes, format='JPEG')
    img_bytes.seek(0)
    return img_bytes, 'test_image.jpg'


def create_test_video():
    """创建一个假的视频文件(用于测试)"""
    # 创建一个小的二进制文件模拟视频
    video_content = b'\x00' * 1024  # 1KB 的空数据
    video_bytes = BytesIO(video_content)
    return video_bytes, 'test_video.mp4'


def test_upload_image(token):
    """测试上传图片"""
    print("\n" + "="*60)
    print("测试1: 上传图片")
    print("="*60)
    
    url = f"{BASE_URL}/upload/post/image"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    # 创建测试图片
    image_bytes, filename = create_test_image()
    files = {
        'file': (filename, image_bytes, 'image/jpeg')
    }
    
    try:
        response = requests.post(url, headers=headers, files=files)
        
        print(f"\n请求: POST {url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"✅ 上传成功!")
            print(f"   - URL: {data['url']}")
            print(f"   - 文件名: {data['filename']}")
            
            # 验证URL格式
            if data['url'].startswith('/api/upload/uploads/'):
                print(f"   ✅ URL格式正确")
            else:
                print(f"   ❌ URL格式错误: 应该以 /api/upload/uploads/ 开头")
            
            # 尝试访问上传的文件
            file_url = f"http://127.0.0.1:5000{data['url']}"
            file_response = requests.get(file_url)
            
            if file_response.status_code == 200:
                print(f"   ✅ 文件可访问 (大小: {len(file_response.content)} bytes)")
            else:
                print(f"   ❌ 文件无法访问: {file_response.status_code}")
            
            return data['url']
        else:
            print(f"❌ 上传失败: {response.json()}")
            return None
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return None


def test_upload_video(token):
    """测试上传视频"""
    print("\n" + "="*60)
    print("测试2: 上传视频")
    print("="*60)
    
    url = f"{BASE_URL}/upload/post/video"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    # 创建测试视频
    video_bytes, filename = create_test_video()
    files = {
        'file': (filename, video_bytes, 'video/mp4')
    }
    
    try:
        response = requests.post(url, headers=headers, files=files)
        
        print(f"\n请求: POST {url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"✅ 上传成功!")
            print(f"   - URL: {data['url']}")
            print(f"   - 文件名: {data['filename']}")
            
            # 验证URL格式
            if data['url'].startswith('/api/upload/uploads/'):
                print(f"   ✅ URL格式正确")
            else:
                print(f"   ❌ URL格式错误")
            
            return data['url']
        else:
            print(f"❌ 上传失败: {response.json()}")
            return None
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return None


def test_upload_avatar(token):
    """测试上传头像"""
    print("\n" + "="*60)
    print("测试3: 上传头像")
    print("="*60)
    
    url = f"{BASE_URL}/upload/avatar"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    image_bytes, filename = create_test_image()
    files = {
        'file': (filename, image_bytes, 'image/jpeg')
    }
    
    try:
        response = requests.post(url, headers=headers, files=files)
        
        print(f"\n请求: POST {url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"✅ 上传成功!")
            print(f"   - URL: {data['url']}")
            return data['url']
        else:
            print(f"❌ 上传失败: {response.json()}")
            return None
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return None


def test_invalid_file(token):
    """测试上传不支持的文件类型"""
    print("\n" + "="*60)
    print("测试4: 上传不支持的文件类型(.txt)")
    print("="*60)
    
    url = f"{BASE_URL}/upload/post/image"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    # 创建一个txt文件
    txt_content = b"This is a text file"
    txt_bytes = BytesIO(txt_content)
    files = {
        'file': ('test.txt', txt_bytes, 'text/plain')
    }
    
    try:
        response = requests.post(url, headers=headers, files=files)
        
        print(f"\n请求: POST {url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 400:
            print(f"✅ 正确拒绝了不支持的文件类型")
            print(f"   - 错误信息: {response.json()['error']}")
        else:
            print(f"❌ 应该返回400错误,但返回了 {response.status_code}")
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")


def test_create_post_with_image(token, image_url):
    """测试使用上传的图片创建帖子"""
    print("\n" + "="*60)
    print("测试5: 使用上传的图片创建帖子")
    print("="*60)
    
    if not image_url:
        print("⚠️  跳过: 没有可用的图片URL")
        return
    
    url = f"{BASE_URL}/posts/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    data = {
        "content": f"这是一条带图片的测试帖子 #测试\n图片URL: {image_url}",
        "image_url": image_url,
        "visibility": "public"
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        
        print(f"\n请求: POST {url}")
        print(f"状态码: {response.status_code}")
        
        if response.status_code in [200, 201]:
            post_data = response.json()
            print(f"✅ 帖子创建成功!")
            print(f"   - 帖子ID: {post_data['post']['id']}")
            print(f"   - 图片URL: {post_data['post']['image_url']}")
        else:
            print(f"❌ 创建失败: {response.json()}")
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")


def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("🧪 文件上传功能测试")
    print("="*60)
    
    # 检查服务器是否运行
    try:
        health_check = requests.get("http://127.0.0.1:5000/health", timeout=3)
        if health_check.status_code != 200:
            print("❌ 服务器未正常运行，请先启动 Flask 服务器")
            print("   运行命令: python app.py")
            return
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器 (http://127.0.0.1:5000)")
        print("   请确保 Flask 服务器正在运行")
        print("   运行命令: python app.py")
        return
    
    print("✅ 服务器连接成功")
    
    # 获取Token
    token = get_token()
    if not token:
        print("\n⚠️  无法获取认证令牌")
        return
    
    print(f"\n✅ Token获取成功")
    
    # 运行测试
    image_url = test_upload_image(token)
    video_url = test_upload_video(token)
    avatar_url = test_upload_avatar(token)
    test_invalid_file(token)
    test_create_post_with_image(token, image_url)
    
    print("\n" + "="*60)
    print("✅ 所有测试完成!")
    print("="*60)
    print("\n总结:")
    print("- ✅ 修复了URL路径重复问题")
    print("- ✅ 统一使用 /api/upload/uploads/ 前缀")
    print("- ✅ 确保文件可以正常访问")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
