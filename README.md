# SIFD
# 科学图像伪造检测系统 (Scientific Forgery Detection)

## 🚀 快速开始

请严格按照以下步骤操作，确保系统正常运行：

1.  **启动后端服务 (Java Spring Boot)**
    ```bash
    # 进入Java项目目录
    cd SIFDJAVA
    # 运行主应用程序
    ./mvnw spring-boot:run
    # 或者使用IDE运行 SIFDJAVA/src/main/java/com/sifd/SifdApplication.java
    ```

2.  **启动AI模型服务 (Python FastAPI)**
    ```bash
    # 进入Python项目目录
    cd SIFD_PYTHON
    # 安装依赖（如果尚未安装）
    pip install -r requirements.txt
    # 修改模型文件路径（重要！）
    # 请打开 sifd_server.py 文件，找到第25行左右的 CONFIG.MODEL_LOC 变量
    # 将其修改为您本地 model_seg_final.pt 文件的正确绝对路径
    # 示例: CONFIG.MODEL_LOC = 'E:/models/model_seg_final.pt'
    # 启动服务
    python sifd_server.py
    ```

3.  **访问前端界面**
    - 打开 `ScientificForgeryDetection.html` 文件
    - 使用以下凭证登录：
      - 用户名: `admin`
      - 密码: `1234`

## 📁 项目结构
├── SIFDJAVA/ # Java后端项目
│ └── src/main/java/com/sifd/
│ └── SifdApplication.java # Spring Boot主类
├── SIFD_PYTHON/ # Python AI服务
│ └── sifd_server.py # FastAPI服务主文件
├── model_seg_final.pt # AI模型权重文件（请放置在此处或自定义路径）
└── ScientificForgeryDetection.html # 前端主界面
