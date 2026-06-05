"""
测试头像和封面上传功能
验证文件上传后是否自动保存到用户资料
"""
import requests
from PIL import Image
from io import BytesIO

BASE_URL = "http://127.0.0.1:5000/api"  # 本地测试


def create_test_image():
    """创建测试图片"""
    img = Image.new('RGB', (200, 200), color=(73, 109, 137))
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)
    return img_byte_arr, "test_avatar.jpg"


def login():
    """登录获取token"""
    print("=" * 60)
    print("步骤1: 登录")
    print("=" * 60)
    
    email = input("请输入邮箱: ")
    password = input("请输入密码: ")
    
    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": email,
        "password": password
    })
    
    if response.status_code == 200:
        token = response.json()["access_token"]
        print("✅ 登录成功")
        return token
    else:
        print(f"❌ 登录失败: {response.text}")
        return None


def get_profile(token):
    """获取当前用户资料"""
    print("\n" + "=" * 60)
    print("步骤2: 获取当前资料")
    print("=" * 60)
    
    response = requests.get(
        f"{BASE_URL}/auth/profile",
        headers={"Authorization": f"Bearer {token}"}
    )
    
    if response.status_code == 200:
        user = response.json()["user"]
        print(f"👤 用户名: {user['username']}")
        print(f"📧 邮箱: {user['email']}")
        print(f"🖼️  头像: {user.get('avatar_url', '无')}")
        print(f"🏞️  封面: {user.get('cover_photo_url', '无')}")
        return user
    else:
        print(f"❌ 获取资料失败: {response.text}")
        return None


def upload_avatar(token):
    """测试上传头像"""
    print("\n" + "=" * 60)
    print("步骤3: 上传头像")
    print("=" * 60)
    
    image_bytes, filename = create_test_image()
    files = {'file': (filename, image_bytes, 'image/jpeg')}
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.post(
        f"{BASE_URL}/upload/avatar",
        headers=headers,
        files=files
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code in [200, 201]:
        data = response.json()
        print(f"✅ 上传成功!")
        print(f"📝 消息: {data['message']}")
        print(f"🔗 URL: {data['url']}")
        return data['url']
    else:
        print(f"❌ 上传失败: {response.text}")
        return None


def upload_cover(token):
    """测试上传封面"""
    print("\n" + "=" * 60)
    print("步骤4: 上传封面")
    print("=" * 60)
    
    # 创建横幅图片 (800x300)
    img = Image.new('RGB', (800, 300), color=(137, 73, 109))
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)
    
    files = {'file': ('test_cover.jpg', img_byte_arr, 'image/jpeg')}
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.post(
        f"{BASE_URL}/upload/cover",
        headers=headers,
        files=files
    )
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code in [200, 201]:
        data = response.json()
        print(f"✅ 上传成功!")
        print(f"📝 消息: {data['message']}")
        print(f"🔗 URL: {data['url']}")
        return data['url']
    else:
        print(f"❌ 上传失败: {response.text}")
        return None


def verify_profile_updated(token):
    """验证资料是否更新"""
    print("\n" + "=" * 60)
    print("步骤5: 验证资料已更新")
    print("=" * 60)
    
    response = requests.get(
        f"{BASE_URL}/auth/profile",
        headers={"Authorization": f"Bearer {token}"}
    )
    
    if response.status_code == 200:
        user = response.json()["user"]
        print(f"👤 用户名: {user['username']}")
        print(f"🖼️  新头像: {user.get('avatar_url', '无')}")
        print(f"🏞️  新封面: {user.get('cover_photo_url', '无')}")
        
        if user.get('avatar_url') and 'avatars' in user['avatar_url']:
            print("✅ 头像URL已更新到数据库!")
        else:
            print("❌ 头像URL未更新")
            
        if user.get('cover_photo_url') and 'covers' in user['cover_photo_url']:
            print("✅ 封面URL已更新到数据库!")
        else:
            print("❌ 封面URL未更新")
    else:
        print(f"❌ 获取资料失败: {response.text}")


def main():
    print("\n🚀 开始测试头像和封面上传功能\n")
    
    # 1. 登录
    token = login()
    if not token:
        return
    
    # 2. 获取当前资料
    get_profile(token)
    
    # 3. 上传头像
    avatar_url = upload_avatar(token)
    
    # 4. 上传封面
    cover_url = upload_cover(token)
    
    # 5. 验证资料已更新
    verify_profile_updated(token)
    
    print("\n" + "=" * 60)
    print("✅ 测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
