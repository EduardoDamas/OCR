# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Bolão OCR desktop app.
Build:  pyinstaller BolaoOCR.spec --noconfirm
Output: dist/BolaoOCR.exe  (single-file, no console window)
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # Flask templates must travel with the exe (app.py uses render_template)
    # plus the trained ML model (run offline via OpenCV DNN)
    datas=[('src/web/templates', 'src/web/templates'),
           ('src/omr/models', 'src/omr/models')],
    # Lazy imports PyInstaller's static analysis can miss
    hiddenimports=[
        'fitz',                 # PyMuPDF — imported inside a function
        'openpyxl',             # imported inside export_excel
        'flask_cors',
        'werkzeug',
        'tkinter', 'tkinter.ttk', 'tkinter.filedialog',
        'tkinter.messagebox', 'tkinter.simpledialog',
        'src.omr.trainer',      # imported lazily inside recognizer/UI functions
        'src.omr.digital_reader',  # imported lazily inside pipeline
        'src.merge',            # imported lazily inside the UI import handler
        'src.web.publisher',    # imported lazily inside the UI publish handler
        'src.web.publish',      # publicar a sessão no link fixo (nuvem 24h)
        'src.omr.ai_reader',    # imported lazily inside pipeline/UI
        'anthropic',            # SDK da leitura por IA (import tardio)
        'anthropic.types.message_create_params',   # usados no modo lote (Batch API)
        'anthropic.types.messages.batch_create_params',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim modules pulled in transitively but never used at runtime
    excludes=['scipy', 'skimage', 'pdf2image', 'matplotlib', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BolaoOCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
