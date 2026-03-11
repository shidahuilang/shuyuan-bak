# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import json
import os
import sys
from datetime import datetime, timedelta
import re
import urllib3
import urllib.parse
import shutil
import pytz
import time
import logging
import hashlib
from typing import List, Tuple, Dict, Optional, Any, Set
from functools import wraps
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed


if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ExecutionStats:
    """执行统计类 - 用于跟踪脚本执行情况"""
    
    def __init__(self):
        self.start_time = time.time()
        self.downloads_success = 0
        self.downloads_failed = 0
        self.pages_parsed = 0
        self.json_files_merged = 0
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def add_success(self):
        """记录成功下载"""
        self.downloads_success += 1
    
    def add_failure(self, error_msg: str):
        """记录失败下载"""
        self.downloads_failed += 1
        self.errors.append(error_msg)
    
    def add_warning(self, warning_msg: str):
        """记录警告"""
        self.warnings.append(warning_msg)
    
    def get_progress_info(self) -> str:
        """获取进度信息字符串"""
        total = self.downloads_success + self.downloads_failed
        if total == 0:
            return "0/0 (0%)"
        percentage = (self.downloads_success / total) * 100
        return f"{self.downloads_success}/{total} ({percentage:.1f}%)"
    
    def get_duration(self) -> float:
        """获取执行时长(秒)"""
        return time.time() - self.start_time
    
    def print_summary(self):
        """打印执行摘要 - GitHub Actions 友好格式"""
        duration = self.get_duration()
        
        print("\n" + "="*60)
        print("📊 执行摘要 | Execution Summary")
        print("="*60)
        print(f"⏱️  执行时长: {duration:.2f} 秒")
        print(f"✅ 成功下载: {self.downloads_success} 个文件")
        print(f"❌ 失败下载: {self.downloads_failed} 个文件")
        print(f"📄 解析页面: {self.pages_parsed} 个")
        print(f"🔗 合并文件: {self.json_files_merged} 个")
        
        if self.warnings:
            print(f"\n⚠️  警告信息 ({len(self.warnings)} 条):")
            for i, warning in enumerate(self.warnings[:5], 1):
                print(f"  {i}. {warning}")
            if len(self.warnings) > 5:
                print(f"  ... 还有 {len(self.warnings) - 5} 条警告")
        
        if self.errors:
            print(f"\n❗ 错误信息 ({len(self.errors)} 条):")
            for i, error in enumerate(self.errors[:5], 1):
                print(f"  {i}. {error}")
            if len(self.errors) > 5:
                print(f"  ... 还有 {len(self.errors) - 5} 条错误")
        
        print("="*60 + "\n")
        

        if os.getenv('GITHUB_ACTIONS'):
            print(f"::notice title=执行完成::成功 {self.downloads_success} | 失败 {self.downloads_failed} | 耗时 {duration:.2f}s")


class Config:
    """配置管理类 - 支持环境变量覆盖"""
    
    def __init__(self, config_path: str = 'py/config.json'):
        """
        初始化配置
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config = self._load_config()
        self._process_config()
        self._apply_env_overrides()
        self._setup_logging()
    
    def _load_config(self) -> Dict[str, Any]:
        """
        加载配置文件
        
        Returns:
            配置字典
        """
        try:
            # 尝试从多个可能的路径加载配置
            possible_paths = [
                self.config_path,
                os.path.join(os.getcwd(), self.config_path),
                os.path.join(os.path.dirname(__file__), 'config.json')
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        logging.info(f"✅ 成功加载配置文件: {path}")
                        return config
            
            # 如果找不到配置文件,使用默认配置
            logging.warning(f"⚠️  未找到配置文件,使用默认配置")
            return self._get_default_config()
            
        except json.JSONDecodeError as e:
            logging.error(f"❌ 配置文件格式错误: {e}")
            return self._get_default_config()
        except Exception as e:
            logging.error(f"❌ 加载配置文件失败: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "request_timeout": 10,
            "max_retries": 3,
            "retry_delay": 1,
            "max_workers": 5,
            "log_level": "INFO",
            "ssl_verify": True
        }
    
    def _process_config(self):
        """处理配置,从环境变量读取URL并转换配置格式"""
        shuyuan_url = os.getenv('SHUYUAN_URL')
        shuyuans_url = os.getenv('SHUYUANS_URL')
        
        if not shuyuan_url:
            raise ValueError(
                "❌ 未设置环境变量 SHUYUAN_URL\n"
                "请在GitHub仓库中配置Variables,或在本地设置环境变量:\n"
                "  Windows: $env:SHUYUAN_URL=\"your-url-here\"\n"
                "  Linux/Mac: export SHUYUAN_URL=\"your-url-here\""
            )
        
        if not shuyuans_url:
            raise ValueError(
                "❌ 未设置环境变量 SHUYUANS_URL\n"
                "请在GitHub仓库中配置Variables,或在本地设置环境变量:\n"
                "  Windows: $env:SHUYUANS_URL=\"your-url-here\"\n"
                "  Linux/Mac: export SHUYUANS_URL=\"your-url-here\""
            )
        
        # 获取备用URL(从环境变量或配置文件)
        # 优先使用环境变量,其次使用配置文件
        shuyuan_fallback = os.getenv('SHUYUAN_FALLBACK_URL')
        shuyuans_fallback = os.getenv('SHUYUANS_FALLBACK_URL')
        
        fallback_config = self.config.get('fallback_urls', {})
        
        # 构建完整的URL列表 (主URL + 备用URLs)
        shuyuan_urls = [shuyuan_url]
        # 环境变量优先
        if shuyuan_fallback:
            shuyuan_urls.append(shuyuan_fallback)
        # 配置文件作为补充
        elif 'shuyuan' in fallback_config and isinstance(fallback_config['shuyuan'], list):
            shuyuan_urls.extend(fallback_config['shuyuan'])
        
        shuyuans_urls = [shuyuans_url]
        # 环境变量优先
        if shuyuans_fallback:
            shuyuans_urls.append(shuyuans_fallback)
        # 配置文件作为补充
        elif 'shuyuans' in fallback_config and isinstance(fallback_config['shuyuans'], list):
            shuyuans_urls.extend(fallback_config['shuyuans'])
        
        # 保存URL列表映射
        self.config['url_lists'] = {
            'shuyuan': shuyuan_urls,
            'shuyuans': shuyuans_urls
        }
        
        # 保持向后兼容
        self.config['urls'] = [shuyuan_url, shuyuans_url]
        
        # 从第一个URL提取基础域名
        from urllib.parse import urlparse
        parsed_url = urlparse(shuyuan_url)
        self.config['base_url'] = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        logging.debug(f"📍 基础域名: {self.config['base_url']}")
        
        time_ranges = {}
        
        # 为所有URL配置时间范围
        shuyuan_days = self.config.get('shuyuan_data', 5)
        if isinstance(shuyuan_days, int) and shuyuan_days > 0:
            for url in shuyuan_urls:
                time_ranges[url] = [1, shuyuan_days]
        else:
            logging.warning(f"⚠️  shuyuan_data 配置错误,使用默认值 5天")
            for url in shuyuan_urls:
                time_ranges[url] = [1, 5]
        
        shuyuans_days = self.config.get('shuyuans_data', 5)
        if isinstance(shuyuans_days, int) and shuyuans_days > 0:
            for url in shuyuans_urls:
                time_ranges[url] = [1, shuyuans_days]
        else:
            logging.warning(f"⚠️  shuyuans_data 配置错误,使用默认值 5天")
            for url in shuyuans_urls:
                time_ranges[url] = [1, 5]
        
        self.config['time_ranges'] = time_ranges
        self.config['output_dirs'] = {
            "shuyuan": "shuyuan_data",
            "shuyuans": "shuyuans_data"
        }
        
        logging.info(f"📋 配置处理完成:")
        logging.info(f"  - shuyuan_url: {shuyuan_url} (时间范围: {time_ranges[shuyuan_url]})")
        if len(shuyuan_urls) > 1:
            logging.info(f"    备用地址: {len(shuyuan_urls) - 1} 个")
        logging.info(f"  - shuyuans_url: {shuyuans_url} (时间范围: {time_ranges[shuyuans_url]})")
        if len(shuyuans_urls) > 1:
            logging.info(f"    备用地址: {len(shuyuans_urls) - 1} 个")
    
    def _apply_env_overrides(self):
        """应用环境变量覆盖配置"""
        # 支持通过环境变量覆盖配置
        if os.getenv('REQUEST_TIMEOUT'):
            self.config['request_timeout'] = int(os.getenv('REQUEST_TIMEOUT'))
        if os.getenv('MAX_RETRIES'):
            self.config['max_retries'] = int(os.getenv('MAX_RETRIES'))
        if os.getenv('LOG_LEVEL'):
            self.config['log_level'] = os.getenv('LOG_LEVEL')
    
    def _setup_logging(self):
        """设置日志系统 - GitHub Actions 友好格式"""
        log_level = self.config.get('log_level', 'INFO')
        
        # GitHub Actions 环境使用更简洁的格式
        if os.getenv('GITHUB_ACTIONS'):
            log_format = '%(levelname)s - %(message)s'
        else:
            log_format = '%(asctime)s - %(levelname)s - %(message)s'
        
        logging.basicConfig(
            level=getattr(logging, log_level),
            format=log_format,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        return self.config.get(key, default)


def retry_on_failure(max_retries: int = 3, delay: int = 1, exponential_backoff: bool = True):
    """
    请求重试装饰器 - 支持指数退避策略
    
    Args:
        max_retries: 最大重试次数
        delay: 初始重试延迟(秒)
        exponential_backoff: 是否使用指数退避
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        # 指数退避: delay * 2^attempt
                        wait_time = delay * (2 ** attempt) if exponential_backoff else delay
                        logging.warning(f"请求失败,{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {e}")
                        time.sleep(wait_time)
                    else:
                        logging.error(f"请求失败,已达最大重试次数: {e}")
                        raise
            return None
        return wrapper
    return decorator


class ShuyuanCrawler:
    """书源爬虫类 - 支持 Session 复用和统计跟踪"""
    
    def __init__(self, config: Config, stats: ExecutionStats):
        """
        初始化爬虫
        
        Args:
            config: 配置对象
            stats: 统计对象
        """
        self.config = config
        self.stats = stats
        self.urls = config.get('urls', [])
        self.url_lists = config.get('url_lists', {})
        self.time_ranges = config.get('time_ranges', {})
        self.timeout = config.get('request_timeout', 10)
        self.ssl_verify = config.get('ssl_verify', True)
        self.max_retries = config.get('max_retries', 3)
        self.retry_delay = config.get('retry_delay', 1)
        
        # 基础URL (从第一个URL提取)
        self.base_url = config.get('base_url', '')
        
        # Session复用
        self.session = requests.Session()
        self.session.verify = self.ssl_verify
        
        # 连接池优化
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        # 去重集合
        self.downloaded_urls: Set[str] = set()
        self.url_hashes: Set[str] = set()
        self.book_names: Set[str] = set()
        
        # 并发控制
        self.max_workers = config.get('max_workers', 5)
    
    def _try_urls_with_fallback(self, url_list: List[str], operation_name: str = "操作") -> Tuple[Optional[Any], str]:
        """
        使用备用URL机制尝试操作
        
        Args:
            url_list: URL列表 (主URL + 备用URLs)
            operation_name: 操作名称(用于日志)
            
        Returns:
            (解析结果, 成功的URL) 元组,如果所有URL都失败则返回 (None, '')
        """
        last_error = None
        
        for i, url in enumerate(url_list):
            try:
                if i > 0:
                    logging.info(f"🔄 切换到备用URL [{i}]: {url}")
                
                # 尝试解析页面
                result = self._parse_page_internal(url)
                
                if i > 0:
                    logging.info(f"✅ 备用URL [{i}] 连接成功")
                
                return result, url
                
            except Exception as e:
                last_error = e
                if i < len(url_list) - 1:
                    logging.warning(f"⚠️  URL [{i}] {operation_name}失败: {e}, 尝试下一个备用地址...")
                else:
                    logging.error(f"❌ 所有URL均失败,最后错误: {e}")
                    self.stats.add_failure(f"所有URL {operation_name}失败: {e}")
        
        return None, ''
    
    def _parse_page_internal(self, url: str) -> List[Tuple[str, datetime.date]]:
        """
        解析页面获取相关链接(内部方法,带重试)
        
        Args:
            url: 页面URL
            
        Returns:
            (JSON URL, 日期) 元组列表
        """
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code != 200:
                    error_msg = f"访问 {url} 失败: HTTP {response.status_code}"
                    if attempt < self.max_retries - 1:
                        logging.warning(f"⚠️  {error_msg}, 重试中...")
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    else:
                        raise Exception(error_msg)
                
                soup = BeautifulSoup(response.text, 'html.parser')
                relevant_links = []
                today = datetime.today().date()
                
                # 从URL提取基础域名
                from urllib.parse import urlparse
                parsed = urlparse(url)
                base_url = f"{parsed.scheme}://{parsed.netloc}"
                
                for div in soup.find_all('div', class_='layui-col-xs12 layui-col-sm6 layui-col-md4'):
                    link = div.find('a', href=True)
                    date_element = div.find('p', class_='m-right')
                    
                    if link and date_element:
                        href = link['href']
                        link_date_str = date_element.text.strip()
                        
                        # 解析相对时间
                        match = re.search(r'(\d+)(天前|小时前|分钟前)', link_date_str)
                        if match:
                            value, unit = match.group(1, 2)
                            if unit in ['分钟前', '小时前']:
                                days_ago = 1
                            else:
                                days_ago = int(value)
                            
                            link_date = today - timedelta(days=days_ago)
                            time_range = self.time_ranges.get(url, (0, float('inf')))
                            
                            if time_range[0] <= days_ago <= time_range[1]:
                                json_url = f'{base_url}{href.replace("content", "json")}'
                                relevant_links.append((json_url, link_date))
                        else:
                            # 解析绝对时间
                            link_date = None
                            
                            try:
                                link_date = datetime.strptime(link_date_str, "%Y/%m/%d")
                            except ValueError:
                                pass
                            
                            if not link_date:
                                try:
                                    link_date = datetime.strptime(link_date_str, "%m/%d %H:%M")
                                    link_date = link_date.replace(year=today.year)
                                except ValueError:
                                    pass
                            
                            if not link_date:
                                try:
                                    link_date = datetime.strptime(link_date_str, "%Y-%m-%d")
                                except ValueError:
                                    pass
                            
                            if link_date:
                                time_range = self.time_ranges.get(url, (0, float('inf')))
                                days_diff = (today - link_date.date()).days
                                
                                if time_range[0] <= days_diff <= time_range[1]:
                                    json_url = f'{base_url}{href.replace("content", "json")}'
                                    relevant_links.append((json_url, link_date.date()))
                                else:
                                    logging.debug(f"⏭️  跳过超出范围的日期: {link_date_str} ({days_diff}天前)")
                            else:
                                warning_msg = f"未知日期格式: {link_date_str}"
                                logging.warning(f"⚠️  {warning_msg}")
                                self.stats.add_warning(warning_msg)
                
                self.stats.pages_parsed += 1
                logging.info(f"✅ 从 {url} 解析到 {len(relevant_links)} 个链接")
                return relevant_links
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logging.warning(f"⚠️  解析失败,{wait_time}秒后重试 ({attempt + 1}/{self.max_retries}): {e}")
                    time.sleep(wait_time)
                else:
                    raise
        
        return []
    
    def parse_page(self, url: str) -> List[Tuple[str, datetime.date]]:
        """
        解析页面获取相关链接(公共接口)
        
        Args:
            url: 页面URL
            
        Returns:
            (JSON URL, 日期) 元组列表
        """
        logging.info(f"🔍 开始解析页面: {url}")
        
        try:
            return self._parse_page_internal(url)
        except Exception as e:
            error_msg = f"解析页面失败: {e}"
            logging.error(f"❌ {error_msg}")
            self.stats.add_warning(error_msg)
            return []
    
    @retry_on_failure(max_retries=3, delay=1)
    def get_redirected_url(self, url: str) -> Optional[str]:
        """
        获取重定向后的URL
        
        Args:
            url: 原始URL
            
        Returns:
            重定向后的URL,失败返回None
        """
        response = self.session.get(url, allow_redirects=False, timeout=self.timeout)
        
        try:
            if response.status_code == 302:
                final_url = response.headers['Location']
                return final_url
            elif response.status_code == 200:
                return url
            else:
                warning_msg = f"意外的状态码 {response.status_code}: {url}"
                logging.warning(f"⚠️  {warning_msg}")
                self.stats.add_warning(warning_msg)
                return None
        except KeyError:
            error_msg = f"获取重定向URL失败: {url}"
            logging.error(f"❌ {error_msg}")
            self.stats.add_failure(error_msg)
            return None
    
    def _get_url_hash(self, url: str) -> str:
        """
        获取URL的哈希值(用于快速去重)
        
        Args:
            url: URL字符串
            
        Returns:
            MD5哈希值
        """
        return hashlib.md5(url.encode('utf-8')).hexdigest()
    
    def download_json(self, url: str, output_base_dir: str = '') -> bool:
        """
        下载 JSON 文件 - 带数据验证和去重
        
        Args:
            url: JSON URL
            output_base_dir: 输出基础目录
            
        Returns:
            下载是否成功
        """
        # 使用哈希快速去重检查
        url_hash = self._get_url_hash(url)
        if url_hash in self.url_hashes:
            logging.debug(f"⏭️  跳过重复 URL: {url}")
            return True
        
        final_url = self.get_redirected_url(url)
        
        if not final_url:
            error_msg = f"无法获取重定向URL: {url}"
            logging.error(f"❌ {error_msg}")
            self.stats.add_failure(error_msg)
            return False
        
        logging.debug(f"🔗 真实URL: {final_url}")
        
        json_url = final_url.replace('.html', '.json')
        
        try:
            response = self.session.get(json_url, timeout=self.timeout)
            
            if response.status_code == 200:
                try:
                    json_content = response.json()
                    
                    # 数据验证
                    if not isinstance(json_content, list):
                        error_msg = f"JSON 格式错误 (非列表): {json_url}"
                        logging.error(f"❌ {error_msg}")
                        self.stats.add_failure(error_msg)
                        return False
                    
                    if len(json_content) == 0:
                        warning_msg = f"空 JSON 文件: {json_url}"
                        logging.warning(f"⚠️  {warning_msg}")
                        self.stats.add_warning(warning_msg)
                        return False
                    
                    filename = os.path.basename(urllib.parse.urlparse(json_url).path)
                    
                    # 根据URL确定输出目录
                    output_dir = 'shuyuan_data' if 'shuyuan' in json_url else 'shuyuans_data'
                    output_path = os.path.join(output_base_dir, output_dir, filename)
                    
                    os.makedirs(os.path.join(output_base_dir, output_dir), exist_ok=True)
                    
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(json_content, f, indent=2, ensure_ascii=False)
                    
                    self.downloaded_urls.add(url)
                    self.url_hashes.add(url_hash)
                    self.stats.add_success()
                    logging.info(f"✅ 成功下载: {filename} ({len(json_content)} 条) -> {output_dir}")
                    return True
                    
                except json.JSONDecodeError as e:
                    error_msg = f"JSON解析失败: {json_url}"
                    logging.error(f"❌ {error_msg} - {e}")
                    self.stats.add_failure(error_msg)
                    return False
            else:
                error_msg = f"下载失败 HTTP {response.status_code}: {json_url}"
                logging.error(f"❌ {error_msg}")
                self.stats.add_failure(error_msg)
                return False
                
        except Exception as e:
            error_msg = f"下载异常: {json_url}"
            logging.error(f"❌ {error_msg} - {e}")
            self.stats.add_failure(error_msg)
            return False
    
    def clean_old_files(self, directory: str = '', root_dir: str = ''):
        """
        清理旧文件
        
        Args:
            directory: 目录名
            root_dir: 根目录
        """
        directory = directory or os.getcwd()
        full_path = os.path.abspath(os.path.join(root_dir, directory))
        
        try:
            if os.path.exists(full_path):
                for filename in os.listdir(full_path):
                    file_path = os.path.join(full_path, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logging.debug(f"删除文件: {file_path}")
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                            logging.debug(f"删除目录: {file_path}")
                    except Exception as e:
                        logging.error(f"删除失败 ({file_path}): {e}")
                
                logging.info(f"成功清理目录: {full_path}")
            else:
                logging.warning(f"目录不存在: {full_path}")
        except OSError as e:
            logging.error(f"清理目录失败 ({full_path}): {e}")
    
    def beautify_json_file(self, file_path: str):
        """
        美化JSON文件
        
        Args:
            file_path: 文件路径
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            logging.debug(f"成功美化文件: {file_path}")
        except Exception as e:
            logging.error(f"美化文件失败 ({file_path}): {e}")
    
    def beautify_json_files(self, directory: str = '', root_dir: str = ''):
        """
        美化目录中的所有JSON文件
        
        Args:
            directory: 目录名
            root_dir: 根目录
        """
        directory = directory or os.getcwd()
        full_path = os.path.join(root_dir, directory)
        
        try:
            if os.path.isfile(full_path):
                self.beautify_json_file(full_path)
                logging.info(f"成功美化JSON文件: {full_path}")
            elif os.path.isdir(full_path):
                for filename in os.listdir(full_path):
                    if filename.endswith('.json'):
                        file_path = os.path.join(full_path, filename)
                        self.beautify_json_file(file_path)
                
                logging.info(f"成功美化目录中的所有JSON文件: {full_path}")
            else:
                logging.warning(f"无效路径: {full_path}")
        except OSError as e:
            logging.error(f"美化JSON文件失败 ({full_path}): {e}")
    
    def process_urls(self, url_key: str, root_dir: str = ''):
        """
        处理URL列表(统一的URL处理逻辑) - 支持备用URL和并发下载
        
        Args:
            url_key: URL类型键 ('shuyuan' 或 'shuyuans')
            root_dir: 根目录
        """
        url_list = self.url_lists.get(url_key, [])
        
        if not url_list:
            logging.error(f"❌ 未找到URL配置: {url_key}")
            return
        
        logging.info(f"🚀 开始处理 {url_key} (主URL + {len(url_list) - 1} 个备用)")
        
        # 使用备用URL机制解析页面
        json_urls, successful_url = self._try_urls_with_fallback(url_list, "页面解析")
        
        if not json_urls:
            logging.error(f"❌ 所有URL均无法解析: {url_key}")
            return
        
        total_urls = len(json_urls)
        logging.info(f"📊 共发现 {total_urls} 个JSON文件待下载")
        
        # 使用线程池并发下载
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有下载任务
            future_to_url = {
                executor.submit(self.download_json, json_url, root_dir): json_url 
                for json_url, _ in json_urls
            }
            
            # 等待所有任务完成并显示进度
            completed = 0
            for future in as_completed(future_to_url):
                completed += 1
                json_url = future_to_url[future]
                try:
                    future.result()
                    # 每完成10个或最后一个时显示进度
                    if completed % 10 == 0 or completed == total_urls:
                        progress = self.stats.get_progress_info()
                        logging.info(f"📈 下载进度: {progress} | 已完成 {completed}/{total_urls}")
                except Exception as e:
                    logging.error(f"❌ 下载任务异常 ({json_url}): {e}")
    
    def merge_json_files(self, input_dir: str = '', output_file: str = 'merged.json', root_dir: str = ''):
        """
        合并JSON文件
        
        Args:
            input_dir: 输入目录
            output_file: 输出文件名
            root_dir: 根目录
        """
        logging.info("🔄 开始合并JSON文件")
        
        input_dir = os.path.join(root_dir, input_dir)
        
        if input_dir and not os.path.exists(input_dir):
            os.makedirs(input_dir)
        
        # 清理旧文件
        self.clean_old_files(directory='shuyuan_data', root_dir=root_dir)
        self.clean_old_files(directory='shuyuans_data', root_dir=root_dir)
        
        # 删除根目录的旧 JSON 文件
        old_json_files = ['shuyuan_data.json', 'shuyuans_data.json', 'book.json']
        for filename in old_json_files:
            file_path = os.path.join(root_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logging.info(f"🗑️  删除旧文件: {filename}")
                except Exception as e:
                    logging.error(f"❌ 删除文件失败 ({filename}): {e}")

        
        # 处理所有URL (使用备用URL机制)
        for url_key in ['shuyuan', 'shuyuans']:
            if url_key in self.url_lists:
                self.process_urls(url_key, root_dir)
        
        # 合并各目录的JSON文件
        for dir_name in ['shuyuan_data', 'shuyuans_data']:
            dir_path = os.path.join(root_dir, dir_name)
            if not os.path.exists(dir_path):
                logging.warning(f"⚠️  目录不存在: {dir_path}")
                continue
            
            all_data = []
            
            for filename in os.listdir(dir_path):
                if filename.endswith('.json'):
                    file_path = os.path.join(dir_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            all_data.extend(data)
                    except Exception as e:
                        logging.error(f"❌ 读取文件失败 ({file_path}): {e}")
            
            output_path = os.path.join(root_dir, f"{dir_name}.json")
            try:
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(all_data, f, indent=2, ensure_ascii=False)
                
                self.stats.json_files_merged += 1
                logging.info(f"✅ 合并完成: {output_path} (共 {len(all_data)} 条书源)")
            except Exception as e:
                logging.error(f"❌ 保存合并文件失败 ({output_path}): {e}")
            
            # 美化JSON文件
            self.beautify_json_files(f"{dir_name}.json", root_dir)
    
    def update_readme_date(self, readme_path: str):
        """
        更新README.md中的时间戳
        
        Args:
            readme_path: README文件路径
        """
        try:
            text_list = []
            tz = pytz.timezone('Asia/Shanghai')
            date_now = datetime.fromtimestamp(int(time.time()), tz).strftime('%Y-%m-%d %H:%M:%S %Z%z')
            
            with open(readme_path, 'r', encoding='UTF-8') as f:
                for line in f.readlines():
                    if re.search('自动更新时间', line):
                        logging.info(f'旧时间戳: {line.strip()}')
                        line = f'**自动更新时间** {date_now}\n'
                        text_list.append(line)
                    else:
                        text_list.append(line)
            
            with open(readme_path, 'w+', encoding='UTF-8') as f:
                for text in text_list:
                    f.write(text)
            
            logging.info(f'成功更新README时间戳: {date_now}')
        except Exception as e:
            logging.error(f"更新README失败: {e}")
    
    def merge_book_json(self, root_dir: str = ''):
        """
        合并书源数据为book.json - 带去重功能
        
        Args:
            root_dir: 根目录
        """
        shuyuan_data_path = os.path.join(root_dir, 'shuyuan_data.json')
        shuyuans_data_path = os.path.join(root_dir, 'shuyuans_data.json')
        book_path = os.path.join(root_dir, 'book.json')
        
        try:
            with open(shuyuan_data_path, 'r', encoding='utf-8') as shuyuan_file, \
                 open(shuyuans_data_path, 'r', encoding='utf-8') as shuyuans_file:
                
                shuyuan_data = json.load(shuyuan_file)
                shuyuans_data = json.load(shuyuans_file)
                
                # 合并数据
                book_data = shuyuan_data + shuyuans_data
                original_count = len(book_data)
                
                # 去重 - 基于bookSourceName和bookSourceUrl
                seen = set()
                unique_data = []
                duplicate_count = 0
                
                for item in book_data:
                    # 创建唯一标识符
                    identifier = (
                        item.get('bookSourceName', ''),
                        item.get('bookSourceUrl', '')
                    )
                    
                    if identifier not in seen and identifier != ('', ''):
                        seen.add(identifier)
                        unique_data.append(item)
                    else:
                        duplicate_count += 1
                
                # 保存去重后的数据
                with open(book_path, 'w', encoding='utf-8') as book_file:
                    json.dump(unique_data, book_file, indent=2, ensure_ascii=False)
                
                logging.info(f"✅ 成功合并为book.json")
                logging.info(f"  📊 原始数据: {original_count} 条")
                logging.info(f"  🛡️  去重后: {len(unique_data)} 条")
                if duplicate_count > 0:
                    logging.info(f"  ♻️  移除重复: {duplicate_count} 条")
        except Exception as e:
            logging.error(f"❌ 合并book.json失败: {e}")


def main():
    """主函数"""
    print("=" * 60)
    print("📚 书源爬取脚本启动")
    print("=" * 60)
    
    # 创建统计对象
    stats = ExecutionStats()
    
    # 加载配置
    config = Config()
    
    # 创建爬虫实例
    crawler = ShuyuanCrawler(config, stats)
    
    # 获取根目录
    root_dir = os.getcwd()
    
    try:
        # 合并JSON文件
        crawler.merge_json_files(root_dir=root_dir)
        
        # 合并书源数据
        crawler.merge_book_json(root_dir=root_dir)
        
        # 更新README时间戳
        readme_path = os.path.join(root_dir, "README.md")
        if os.path.exists(readme_path):
            crawler.update_readme_date(readme_path)
        else:
            logging.warning(f"README.md不存在: {readme_path}")
        
        # 打印执行摘要
        stats.print_summary()
        
        print("=" * 60)
        print("✅ 书源爬取完成!")
        print("=" * 60)
        
    except Exception as e:
        logging.error(f"❌ 执行过程中发生错误: {e}", exc_info=True)
        stats.print_summary()
        raise


if __name__ == "__main__":
    main()
