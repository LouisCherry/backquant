#!/usr/bin/env python3
"""应用层数据清洗与治理工具

功能：
1. 扫描并清洗应用系统产生的脏数据（如报表暂存数据、业务逻辑异常数据）
2. 执行去重、缺失值处理、异常值检测、格式标准化等操作
3. 生成详细的清洗报告
4. 支持将清洗后的数据转换为 Parquet 格式

使用示例：
    # 清洗指定目录
    python scripts/clean_application_data.py --input-dir data/temp
    
    # 清洗多个目录
    python scripts/clean_application_data.py --input-dir data/temp --input-dir data/user_import
    
    # 生成清洗报告
    python scripts/clean_application_data.py --input-dir data/temp --output-report cleaning_report.json
    
    # 清洗后转换为 Parquet
    python scripts/clean_application_data.py --input-dir data/temp --convert-to-parquet
"""
import os
import sys
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

import pandas as pd
import numpy as np

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import CONFIG

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 数据目录配置
DEFAULT_INPUT_DIRS = [
    'data/temp',
    'data/user_import',
    'data/cache',
    'data/imports'
]

# 支持的文件类型
SUPPORTED_FILES = ['.csv', '.xlsx', '.xls', '.parquet', '.h5']

# 关键字段配置
KEY_FIELDS = {
    'stock': ['code', 'date', 'close', 'open', 'high', 'low', 'volume'],
    'financial': ['code', 'date', 'pe', 'pb', 'roe', 'revenue', 'profit'],
    'factor': ['code', 'date', 'factor_value', 'signal']
}

# 异常值检测配置
OUTLIER_RULES = {
    'pe': {'min': 0, 'max': 1000},
    'pb': {'min': 0, 'max': 100},
    'close': {'min': 0.01},
    'volume': {'min': 0},
    'change_pct': {'min': -20, 'max': 20}  # 涨跌幅限制在 ±20%
}


class DataCleaner:
    """数据清洗类"""
    
    def __init__(self, input_dirs: List[Path], output_report: Optional[Path] = None):
        self.input_dirs = input_dirs
        self.output_report = output_report
        self.report = {
            'total_files': 0,
            'processed_files': 0,
            'cleaned_files': 0,
            'summary': {
                'original_rows': 0,
                'duplicate_rows': 0,
                'removed_rows': 0,
                'outlier_rows': 0,
                'final_rows': 0,
                'missing_values': {},
                'outliers': {}
            },
            'details': {}
        }
    
    def _detect_file_type(self, file_path: Path) -> Optional[str]:
        """检测文件类型"""
        ext = file_path.suffix.lower()
        if ext in SUPPORTED_FILES:
            return ext
        return None
    
    def _read_file(self, file_path: Path) -> Optional[pd.DataFrame]:
        """读取文件为 DataFrame"""
        ext = self._detect_file_type(file_path)
        if not ext:
            logger.warning(f"不支持的文件类型: {file_path}")
            return None
        
        try:
            if ext == '.csv':
                return pd.read_csv(file_path)
            elif ext in ['.xlsx', '.xls']:
                return pd.read_excel(file_path)
            elif ext == '.parquet':
                return pd.read_parquet(file_path)
            elif ext == '.h5':
                return pd.read_hdf(file_path)
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
        
        return None
    
    def _detect_data_type(self, df: pd.DataFrame) -> str:
        """检测数据类型"""
        columns = set(df.columns.str.lower())
        
        # 检测股票行情数据
        stock_fields = set([f.lower() for f in KEY_FIELDS['stock']])
        if 'code' in columns and 'date' in columns and 'close' in columns:
            return 'stock'
        
        # 检测财务数据
        financial_fields = set([f.lower() for f in KEY_FIELDS['financial']])
        if 'code' in columns and 'date' in columns and any(f in columns for f in ['pe', 'pb', 'roe']):
            return 'financial'
        
        # 检测因子数据
        factor_fields = set([f.lower() for f in KEY_FIELDS['factor']])
        if 'code' in columns and 'date' in columns and 'factor_value' in columns:
            return 'factor'
        
        return 'generic'
    
    def _clean_data(self, df: pd.DataFrame, file_path: Path, data_type: str) -> pd.DataFrame:
        """清洗数据"""
        file_name = file_path.name
        self.report['details'][file_name] = {
            'original_rows': len(df),
            'duplicate_rows': 0,
            'removed_rows': 0,
            'outlier_rows': 0,
            'final_rows': 0,
            'missing_values': {},
            'outliers': {}
        }
        
        original_rows = len(df)
        
        # 1. 去重
        if 'code' in df.columns and 'date' in df.columns:
            # 基于 code + date 去重
            before_duplicates = len(df)
            df = df.drop_duplicates(subset=['code', 'date'], keep='last')
            duplicates_removed = before_duplicates - len(df)
            self.report['details'][file_name]['duplicate_rows'] = duplicates_removed
            self.report['summary']['duplicate_rows'] += duplicates_removed
            if duplicates_removed > 0:
                logger.info(f"{file_name}: 删除重复行 {duplicates_removed} 行")
        
        # 2. 缺失值处理
        missing_stats = {}
        for col in df.columns:
            missing_count = df[col].isna().sum()
            missing_rate = missing_count / len(df) if len(df) > 0 else 0
            missing_stats[col] = {
                'count': int(missing_count),
                'rate': float(missing_rate)
            }
            
            # 处理缺失值
            if missing_rate > 0:
                if missing_rate > 0.5:
                    logger.warning(f"{file_name}: {col} 缺失率 {missing_rate:.2%}，标记为严重缺失")
                elif missing_rate < 0.1:
                    # 低缺失率，尝试填充
                    if pd.api.types.is_numeric_dtype(df[col]):
                        df[col] = df[col].fillna(0)
                    elif pd.api.types.is_datetime64_any_dtype(df[col]):
                        df[col] = df[col].fillna(method='ffill')
        
        self.report['details'][file_name]['missing_values'] = missing_stats
        self.report['summary']['missing_values'] = {**self.report['summary']['missing_values'], **missing_stats}
        
        # 3. 异常值检测
        outlier_stats = {}
        for col, rules in OUTLIER_RULES.items():
            if col in df.columns:
                outliers = 0
                if 'min' in rules:
                    outliers += (df[col] < rules['min']).sum()
                    df.loc[df[col] < rules['min'], col] = np.nan
                if 'max' in rules:
                    outliers += (df[col] > rules['max']).sum()
                    df.loc[df[col] > rules['max'], col] = np.nan
                if outliers > 0:
                    outlier_stats[col] = int(outliers)
                    logger.info(f"{file_name}: 修正 {col} 异常值 {outliers} 个")
        
        self.report['details'][file_name]['outlier_rows'] = sum(outlier_stats.values())
        self.report['summary']['outlier_rows'] += sum(outlier_stats.values())
        self.report['details'][file_name]['outliers'] = outlier_stats
        self.report['summary']['outliers'] = {**self.report['summary']['outliers'], **outlier_stats}
        
        # 4. 格式标准化
        # 统一日期格式
        if 'date' in df.columns:
            try:
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                logger.info(f"{file_name}: 统一日期格式为 YYYY-MM-DD")
            except Exception as e:
                logger.warning(f"{file_name}: 日期格式标准化失败: {e}")
        
        # 统一股票代码格式
        if 'code' in df.columns:
            # 去除 .SH/.SZ 后缀
            df['code'] = df['code'].astype(str).apply(lambda x: x.split('.')[0] if '.' in x else x)
            # 去除空格和特殊字符
            df['code'] = df['code'].str.strip()
            logger.info(f"{file_name}: 统一股票代码格式")
        
        # 5. 最终数据量
        final_rows = len(df)
        removed_rows = original_rows - final_rows
        
        self.report['details'][file_name]['removed_rows'] = removed_rows
        self.report['details'][file_name]['final_rows'] = final_rows
        
        self.report['summary']['original_rows'] += original_rows
        self.report['summary']['removed_rows'] += removed_rows
        self.report['summary']['final_rows'] += final_rows
        
        logger.info(f"{file_name}: 清洗完成 - 原数据 {original_rows} 行，最终 {final_rows} 行")
        
        return df
    
    def _save_cleaned_data(self, df: pd.DataFrame, file_path: Path):
        """保存清洗后的数据"""
        output_dir = file_path.parent / 'cleaned'
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = output_dir / f"cleaned_{file_path.name}"
        
        try:
            if file_path.suffix.lower() == '.csv':
                df.to_csv(output_path, index=False, encoding='utf-8')
            elif file_path.suffix.lower() in ['.xlsx', '.xls']:
                df.to_excel(output_path, index=False)
            elif file_path.suffix.lower() == '.parquet':
                df.to_parquet(output_path, index=False)
            
            logger.info(f"清洗后数据已保存到: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存清洗后数据失败 {output_path}: {e}")
            return False
    
    def _convert_to_parquet(self, df: pd.DataFrame, file_path: Path):
        """转换为 Parquet 格式"""
        output_dir = Path(CONFIG['default'].DATA_PATH_PARQUET) / 'cleaned'
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成唯一的文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = output_dir / f"{file_path.stem}_{timestamp}.parquet"
        
        try:
            df.to_parquet(output_path, index=False, compression='snappy')
            logger.info(f"数据已转换为 Parquet 格式: {output_path}")
            return True
        except Exception as e:
            logger.error(f"转换为 Parquet 失败 {output_path}: {e}")
            return False
    
    def clean(self, convert_to_parquet: bool = False):
        """执行清洗流程"""
        logger.info("=" * 80)
        logger.info("开始应用层数据清洗")
        logger.info("=" * 80)
        
        for input_dir in self.input_dirs:
            if not input_dir.exists():
                logger.warning(f"目录不存在: {input_dir}")
                continue
            
            logger.info(f"扫描目录: {input_dir}")
            
            # 递归查找文件
            for file_path in input_dir.rglob('*'):
                if file_path.is_file():
                    ext = self._detect_file_type(file_path)
                    if ext:
                        self.report['total_files'] += 1
                        logger.info(f"处理文件: {file_path}")
                        
                        # 读取文件
                        df = self._read_file(file_path)
                        if df is not None and len(df) > 0:
                            self.report['processed_files'] += 1
                            
                            # 检测数据类型
                            data_type = self._detect_data_type(df)
                            logger.info(f"数据类型: {data_type}")
                            
                            # 清洗数据
                            cleaned_df = self._clean_data(df, file_path, data_type)
                            
                            # 保存清洗后的数据
                            if self._save_cleaned_data(cleaned_df, file_path):
                                self.report['cleaned_files'] += 1
                                
                                # 转换为 Parquet
                                if convert_to_parquet:
                                    self._convert_to_parquet(cleaned_df, file_path)
        
        # 生成报告
        self._generate_report()
        
        logger.info("=" * 80)
        logger.info("数据清洗完成")
        logger.info("=" * 80)
        return self.report
    
    def _generate_report(self):
        """生成清洗报告"""
        # 添加时间戳
        self.report['timestamp'] = datetime.now().isoformat()
        
        # 计算清洗效率
        if self.report['summary']['original_rows'] > 0:
            cleaning_rate = 1 - (self.report['summary']['final_rows'] / self.report['summary']['original_rows'])
            self.report['summary']['cleaning_rate'] = float(cleaning_rate)
        else:
            self.report['summary']['cleaning_rate'] = 0.0
        
        # 打印报告
        logger.info("\n" + "=" * 60)
        logger.info("数据清洗报告")
        logger.info("=" * 60)
        logger.info(f"处理文件数: {self.report['processed_files']}/{self.report['total_files']}")
        logger.info(f"原数据量: {self.report['summary']['original_rows']} 行")
        logger.info(f"删除重复: {self.report['summary']['duplicate_rows']} 行")
        logger.info(f"修正异常值: {self.report['summary']['outlier_rows']} 行")
        logger.info(f"最终有效数据: {self.report['summary']['final_rows']} 行")
        logger.info(f"清洗率: {self.report['summary']['cleaning_rate']:.2%}")
        logger.info("=" * 60)
        
        # 保存报告到文件
        if self.output_report:
            try:
                with open(self.output_report, 'w', encoding='utf-8') as f:
                    json.dump(self.report, f, indent=2, ensure_ascii=False)
                logger.info(f"清洗报告已保存到: {self.output_report}")
            except Exception as e:
                logger.error(f"保存报告失败: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='应用层数据清洗与治理工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 清洗指定目录
  python scripts/clean_application_data.py --input-dir data/temp
  
  # 清洗多个目录
  python scripts/clean_application_data.py --input-dir data/temp --input-dir data/user_import
  
  # 生成清洗报告
  python scripts/clean_application_data.py --input-dir data/temp --output-report cleaning_report.json
  
  # 清洗后转换为 Parquet
  python scripts/clean_application_data.py --input-dir data/temp --convert-to-parquet
        """
    )
    
    parser.add_argument('--input-dir', action='append', dest='input_dirs',
                        help='输入目录路径（可多次指定）')
    parser.add_argument('--output-report', type=str,
                        default='cleaning_report.json',
                        help='清洗报告输出路径')
    parser.add_argument('--convert-to-parquet', action='store_true',
                        help='将清洗后的数据转换为 Parquet 格式')
    
    args = parser.parse_args()
    
    # 处理输入目录
    if args.input_dirs:
        input_dirs = [Path(d) for d in args.input_dirs]
    else:
        # 使用默认目录
        input_dirs = []
        for dir_path in DEFAULT_INPUT_DIRS:
            p = Path(__file__).parent.parent / dir_path
            if p.exists():
                input_dirs.append(p)
        
        if not input_dirs:
            logger.warning("未找到默认数据目录，使用当前目录")
            input_dirs = [Path('.')]
    
    # 初始化清洗器
    output_report = Path(args.output_report)
    cleaner = DataCleaner(input_dirs, output_report)
    
    # 执行清洗
    cleaner.clean(convert_to_parquet=args.convert_to_parquet)


if __name__ == "__main__":
    main()
