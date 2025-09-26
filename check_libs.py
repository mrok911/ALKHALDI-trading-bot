import importlib
import subprocess
import sys

# دالة لتثبيت المكتبات الناقصة
def install_if_missing(package):
    try:
        importlib.import_module(package)
    except ImportError:
        print(f"⚠️ المكتبة '{package}' غير مثبّتة.. جاري التثبيت")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# المكتبات المطلوبة
required_libs = ["ccxt", "pandas", "numpy", "pandas_ta", "requests"]

for lib in required_libs:
    install_if_missing(lib)

# الآن نقدر نكمل الكود عادي
import ccxt
import pandas as pd
import pandas_ta as ta
import requests

print("✅ كل المكتبات مثبتة وجاهزة للاستخدام!")
