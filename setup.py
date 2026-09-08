from setuptools import find_packages, setup


setup(
    name="quant-audit-core",
    version="0.1.0",
    description="Auditable daily-frequency paper-trading core",
    python_requires=">=3.9",
    packages=find_packages(),
    install_requires=["duckdb>=1.0.0", "pytz>=2024.1"],
    extras_require={"dev": ["pytest>=8.0.0"], "data": ["baostock>=0.8.9", "akshare>=1.18.0", "tushare>=1.4.24"]},
)
