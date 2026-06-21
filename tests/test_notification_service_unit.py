import inspect
import unittest

from app.services.notification_service import NotificationService


class NotificationServiceUnitTests(unittest.TestCase):
    def test_push_new_notification_does_not_depend_on_detached_orm_object(self):
        source = inspect.getsource(NotificationService._push_new_notification)

        self.assertIn("notification_dict", source)
        self.assertNotIn("notification.to_dict()", source)
        self.assertNotIn("getattr(notification,", source)

    def test_create_notification_passes_plain_dict_to_async_push(self):
        source = inspect.getsource(NotificationService.create_notification)

        self.assertIn("notification_dict = notification.to_dict()", source)
        self.assertIn("_push_new_notification(user_id, notification_dict)", source)


if __name__ == "__main__":
    unittest.main()
