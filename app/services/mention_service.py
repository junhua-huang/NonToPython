import re
from app.models.models import User


class MentionService:
    """@提及服务类"""
    
    @staticmethod
    def extract_mentions(content):
        """
        从内容中提取@用户
        
        Args:
            content: 文本内容
        
        Returns:
            list: 用户名列表
        """
        if not content:
            return []
        
        # 匹配 @用户名 格式（支持中英文、数字、下划线）
        mentions = re.findall(r'@([\w\u4e00-\u9fa5]+)', content)
        return list(set(mentions))  # 去重
    
    @staticmethod
    def find_users_by_usernames(usernames):
        """
        根据用户名查找用户
        
        Args:
            usernames: 用户名列表
        
        Returns:
            list: 用户对象列表
        """
        if not usernames:
            return []
        
        users = User.query.filter(
            User.username.in_(usernames),
            User.is_active == True
        ).all()
        
        return users
    
    @staticmethod
    def process_mentions(content, author_id, post_id=None, comment_id=None):
        """
        处理@提及，发送通知
        
        Args:
            content: 文本内容
            author_id: 作者ID
            post_id: 帖子ID（可选）
            comment_id: 评论ID（可选）
        
        Returns:
            list: 被提及的用户列表
        """
        if not content:
            return []
        
        # 提取@用户
        usernames = MentionService.extract_mentions(content)
        
        if not usernames:
            return []
        
        # 查找用户
        users = MentionService.find_users_by_usernames(usernames)
        
        mentioned_users = []
        
        for user in users:
            # 不给自己发通知
            if user.id == author_id:
                continue
            
            # 构建上下文
            context = content[:100] + '...' if len(content) > 100 else content
            
            # 发送通知
            from app.services.notification_service import NotificationService
            NotificationService.notify_mention(
                mentioned_user_id=user.id,
                mentioner_id=author_id,
                post_id=post_id,
                context=context
            )
            
            mentioned_users.append(user)
        
        return mentioned_users
    
    @staticmethod
    def format_mention_notification(mentioned_user, mentioner, content):
        """
        格式化@提及通知
        
        Args:
            mentioned_user: 被提及的用户
            mentioner: 提及者
            content: 内容片段
        
        Returns:
            dict: 通知数据
        """
        preview = content[:50] + '...' if len(content) > 50 else content
        
        return {
            'type': 'mention',
            'title': f'{mentioner.username} 提到了你',
            'content': preview,
            'mentioned_user': mentioned_user.to_dict(),
            'mentioner': mentioner.to_dict()
        }
