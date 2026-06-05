from PIL import Image
import io
import os


class ImageOptimizer:
    """图片优化服务"""
    
    # 默认配置
    MAX_WIDTH = 1920
    MAX_HEIGHT = 1080
    QUALITY = 85
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    
    @staticmethod
    def optimize_image(image_file, max_width=None, max_height=None, quality=None):
        """
        优化图片
        
        Args:
            image_file: 图片文件对象或路径
            max_width: 最大宽度（默认1920）
            max_height: 最大高度（默认1080）
            quality: JPEG质量（默认85）
        
        Returns:
            dict: {
                'success': bool,
                'data': bytes (优化后的图片数据),
                'original_size': int,
                'optimized_size': int,
                'compression_ratio': float,
                'width': int,
                'height': int,
                'format': str
            }
        """
        try:
            max_width = max_width or ImageOptimizer.MAX_WIDTH
            max_height = max_height or ImageOptimizer.MAX_HEIGHT
            quality = quality or ImageOptimizer.QUALITY
            
            # 读取图片
            if hasattr(image_file, 'read'):
                # 文件对象
                original_data = image_file.read()
                image = Image.open(io.BytesIO(original_data))
            else:
                # 文件路径
                with open(image_file, 'rb') as f:
                    original_data = f.read()
                image = Image.open(io.BytesIO(original_data))
            
            original_size = len(original_data)
            
            # 转换颜色模式（如果需要）
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')
            
            # 计算缩放比例
            width, height = image.size
            scale_w = max_width / width if width > max_width else 1
            scale_h = max_height / height if height > max_height else 1
            scale = min(scale_w, scale_h)
            
            # 缩放图片
            if scale < 1:
                new_width = int(width * scale)
                new_height = int(height * scale)
                image = image.resize((new_width, new_height), Image.LANCZOS)
            
            # 保存优化后的图片
            output = io.BytesIO()
            
            # 根据格式选择保存方式
            if image.format == 'PNG':
                image.save(output, format='PNG', optimize=True)
            elif image.format == 'GIF':
                image.save(output, format='GIF', optimize=True)
            else:
                # 默认使用JPEG
                image.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
            
            optimized_data = output.getvalue()
            optimized_size = len(optimized_data)
            
            # 如果文件仍然太大，降低质量重新压缩
            if optimized_size > ImageOptimizer.MAX_FILE_SIZE:
                quality = max(30, quality - 20)
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
                optimized_data = output.getvalue()
                optimized_size = len(optimized_data)
            
            compression_ratio = (1 - optimized_size / original_size) * 100 if original_size > 0 else 0
            
            return {
                'success': True,
                'data': optimized_data,
                'original_size': original_size,
                'optimized_size': optimized_size,
                'compression_ratio': round(compression_ratio, 2),
                'width': image.width,
                'height': image.height,
                'format': image.format or 'JPEG'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def generate_thumbnail(image_file, size=(200, 200)):
        """
        生成缩略图
        
        Args:
            image_file: 图片文件对象或路径
            size: 缩略图尺寸 (宽, 高)
        
        Returns:
            dict: {
                'success': bool,
                'data': bytes (缩略图数据),
                'width': int,
                'height': int
            }
        """
        try:
            # 读取图片
            if hasattr(image_file, 'read'):
                image = Image.open(io.BytesIO(image_file.read()))
            else:
                image = Image.open(image_file)
            
            # 转换颜色模式
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')
            
            # 生成缩略图（保持宽高比）
            image.thumbnail(size, Image.LANCZOS)
            
            # 保存
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=80, optimize=True)
            
            return {
                'success': True,
                'data': output.getvalue(),
                'width': image.width,
                'height': image.height
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def compress_image_file(file_path, output_path=None, quality=None):
        """
        压缩图片文件
        
        Args:
            file_path: 原图片路径
            output_path: 输出路径（默认为覆盖原文件）
            quality: JPEG质量
        
        Returns:
            dict: 压缩结果
        """
        try:
            result = ImageOptimizer.optimize_image(file_path, quality=quality)
            
            if not result['success']:
                return result
            
            # 确定输出路径
            if not output_path:
                output_path = file_path
            
            # 写入文件
            with open(output_path, 'wb') as f:
                f.write(result['data'])
            
            result['output_path'] = output_path
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def get_image_info(image_file):
        """
        获取图片信息
        
        Args:
            image_file: 图片文件对象或路径
        
        Returns:
            dict: 图片信息
        """
        try:
            if hasattr(image_file, 'read'):
                image = Image.open(io.BytesIO(image_file.read()))
                image_file.seek(0)  # 重置文件指针
            else:
                image = Image.open(image_file)
            
            return {
                'success': True,
                'width': image.width,
                'height': image.height,
                'format': image.format,
                'mode': image.mode,
                'size_kb': os.path.getsize(image_file if isinstance(image_file, str) else '') / 1024
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
