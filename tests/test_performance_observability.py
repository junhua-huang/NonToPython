import unittest


class PerformanceObservabilitySourceTest(unittest.TestCase):
    def test_main_registers_request_timing_middleware(self):
        with open('app/main.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('async def log_request_timing', source)
        self.assertIn('PERF_SLOW_REQUEST_MS', source)
        self.assertIn('elapsed_ms', source)
        self.assertIn('_perf_logger.warning', source)
        self.assertIn('_get_perf_slow_request_ms', source)

    def test_security_headers_middleware_still_exists(self):
        with open('app/main.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('async def add_security_headers', source)
        self.assertIn('Cross-Origin-Opener-Policy', source)


if __name__ == '__main__':
    unittest.main()
