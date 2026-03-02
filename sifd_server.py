import os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import uvicorn
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoImageProcessor, AutoModel
import cv2
import numpy as np
import base64
from PIL import Image
import io
import math
from pathlib import Path


# --- 1. 配置参数 (参考了你提供的代码) ---
class CONFIG:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # 权重文件路径
    MODEL_LOC = 'kaggle-image-forgery-detection-main/kaggle-image-forgery-detection-main/model/model_seg_final.pt'
    # 固定输入尺寸 (DINOv2 patch size=14, 518/14=37)
    IMG_SIZE = 518
    # 判定阈值 (来自代码中的最佳实践)
    AREA_THR = 200  # 只有篡改面积超过200像素才算异常
    MEAN_THR = 0.22  # 只有区域内的平均置信度超过0.22才算异常
    USE_TTA = False  # 是否开启TTA (开启会变慢但更准，演示建议False)


# --- 2. 模型定义 (完全复刻 provided code) ---
class DinoTinyDecoder(nn.Module):
    def __init__(self, in_ch=768, out_ch=1):
        super().__init__()
        self.block1 = nn.Sequential(nn.Conv2d(in_ch, 384, 3, 1, 1), nn.ReLU(True), nn.Dropout2d(0.1))
        self.block2 = nn.Sequential(nn.Conv2d(384, 192, 3, 1, 1), nn.ReLU(True), nn.Dropout2d(0.1))
        self.block3 = nn.Sequential(nn.Conv2d(192, 96, 3, 1, 1), nn.ReLU(True))
        self.conv_out = nn.Conv2d(96, out_ch, kernel_size=1)

    def forward(self, f, target_size):
        x = F.interpolate(self.block1(f), size=(74, 74), mode='bilinear', align_corners=False)
        x = F.interpolate(self.block2(x), size=(148, 148), mode='bilinear', align_corners=False)
        x = F.interpolate(self.block3(x), size=(296, 296), mode='bilinear', align_corners=False)
        x = self.conv_out(x)
        x = F.interpolate(x, size=target_size, mode='bilinear', align_corners=False)
        return x


class DinoSegmenter(nn.Module):
    def __init__(self, encoder, processor):
        super().__init__()
        self.encoder, self.processor = encoder, processor
        self.seg_head = DinoTinyDecoder(768, 1)

    def forward_seg(self, x):
        # 预处理转换
        imgs = (x * 255).clamp(0, 255).byte().permute(0, 2, 3, 1).cpu().numpy()
        inputs = self.processor(images=list(imgs), return_tensors="pt").to(x.device)
        # 提取特征
        feats = self.encoder(**inputs).last_hidden_state
        B, N, C = feats.shape
        fmap = feats[:, 1:, :].permute(0, 2, 1)
        s = int(math.sqrt(N - 1))
        fmap = fmap.reshape(B, C, s, s)
        return self.seg_head(fmap, (CONFIG.IMG_SIZE, CONFIG.IMG_SIZE))


# --- 3. 初始化加载 ---
print(f"🚀 正在加载 DINOv2 (device={CONFIG.device})...")
try:
    # 自动下载模型，不再依赖 Kaggle 本地路径
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    encoder = AutoModel.from_pretrained("facebook/dinov2-base").eval().to(CONFIG.device)
    model = DinoSegmenter(encoder, processor).to(CONFIG.device)

    if os.path.exists(CONFIG.MODEL_LOC):
        state_dict = torch.load(CONFIG.MODEL_LOC, map_location=CONFIG.device)
        model.load_state_dict(state_dict)
        print(f"✅ 成功加载权重: {CONFIG.MODEL_LOC}")
    else:
        print(f"⚠️ 警告: 找不到 {CONFIG.MODEL_LOC}，将使用随机权重（测试用）！")
    model.eval()
except Exception as e:
    print(f"❌ 模型初始化失败: {e}")


# --- 4. 核心逻辑 (移植自你提供的代码) ---

@torch.no_grad()
def segment_prob_map(pil_img):
    """基础推理：Resize -> Normalize -> Forward"""
    # 强制 Resize 到 518，解决维度不匹配问题
    img_resized = pil_img.resize((CONFIG.IMG_SIZE, CONFIG.IMG_SIZE))
    x = torch.from_numpy(np.array(img_resized, np.float32) / 255.).permute(2, 0, 1)[None].to(CONFIG.device)
    prob = torch.sigmoid(model.forward_seg(x))[0, 0].cpu().numpy()
    return prob


def enhanced_adaptive_mask(prob, alpha_grad=0.45):
    """
    高级后处理 (来自 Kaggle 代码)：
    1. Sobel 边缘增强
    2. 高斯模糊
    3. 自适应阈值
    4. 形态学闭运算 (填补空洞) + 开运算 (去除噪点)
    """
    # 1. 边缘增强
    gx = cv2.Sobel(prob, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(prob, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(gx ** 2 + gy ** 2)
    grad_norm = grad_mag / (grad_mag.max() + 1e-6)
    enhanced = (1 - alpha_grad) * prob + alpha_grad * grad_norm

    # 2. 平滑
    enhanced = cv2.GaussianBlur(enhanced, (3, 3), 0)

    # 3. 动态阈值
    thr = np.mean(enhanced) + 0.3 * np.std(enhanced)

    # 4. 生成二值掩膜 (0 或 1)
    mask = (enhanced > thr).astype(np.uint8)

    # 5. 形态学操作 (关键优化点：去噪)
    # 闭运算：连接断开的区域
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    # 开运算：去除孤立的小噪点
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    return enhanced, mask, thr


def generate_transparent_heatmap(enhanced_map, mask, orig_size):
    """
    生成带透明度的 PNG 热力图
    enhanced_map: 增强后的概率图 [518, 518]
    mask: 二值化后的掩膜 [518, 518]
    """
    # 恢复原图尺寸
    prob_resized = cv2.resize(enhanced_map, orig_size)
    mask_resized = cv2.resize(mask, orig_size, interpolation=cv2.INTER_NEAREST)

    # 制作热力图颜色
    heatmap_uint8 = (prob_resized * 255).astype(np.uint8)
    heatmap_color = cv2.applyColorMap(heatmap_uint8, cv2.COLORMAP_JET)

    # 制作 Alpha 通道
    # 逻辑：只有在 mask=1 (被判定为篡改) 的区域才显示高亮，其他区域完全透明
    # 这样可以完美解决“全蓝”遮挡原图的问题
    alpha = np.zeros_like(prob_resized, dtype=np.uint8)

    # 只有 mask 区域显示，透明度随概率值变化，最低 150，最高 255
    alpha[mask_resized == 1] = (prob_resized[mask_resized == 1] * 105 + 150).astype(np.uint8)

    # 合并通道
    b, g, r = cv2.split(heatmap_color)
    heatmap_rgba = cv2.merge([b, g, r, alpha])

    return heatmap_rgba


# --- 5. API 接口 ---
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from fastapi import Form  # 记得导入 Form



# 修改 predict 函数签名，接收动态参数
@app.post("/predict")
async def predict(
        file: UploadFile = File(...),
        area_thr: int = Form(200),  # 接收 Java 传来的参数，默认 200
        threshold: float = Form(0.22)  # 接收 Java 传来的参数，默认 0.22 (对应 MEAN_THR)
):
    print(f"🔍 请求处理: {file.filename} | 阈值: Area={area_thr}, Mean={threshold}")

    # ... (读取图片、推理代码不变) ...
    img_bytes = await file.read()
    pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    prob_raw = segment_prob_map(pil_img)
    prob_enhanced, mask, thr = enhanced_adaptive_mask(prob_raw)

    # ... (计算指标代码不变) ...
    area = int(mask.sum())
    mask_bool = (mask == 1)
    mean_inside = float(prob_raw[mask_bool].mean()) if area > 0 else 0.0

    # 4. [修改] 使用动态传入的阈值进行判定
    is_forged = False
    if area >= area_thr and mean_inside >= threshold:
        is_forged = True
        type_str = "疑似篡改 (Copy-Move)"
    else:
        type_str = "正常 (未发现篡改)"


    # 5. 生成可视化图
    # 如果是正常图片，也返回热力图，但是 Alpha 通道全黑（全透明），前端看起来就是原图
    heatmap_rgba = generate_transparent_heatmap(prob_enhanced, mask, pil_img.size)

    # 6. 编码返回
    _, buffer = cv2.imencode('.png', heatmap_rgba)
    heatmap_b64 = base64.b64encode(buffer).decode('utf-8')

    # 计算给前端展示的分数 (最大异常点的概率)
    score = float(np.max(prob_enhanced) * 100) if is_forged else float(mean_inside * 100)

    return {
        "code": 200,
        "data": {
            "type": type_str,
            "score": round(score, 2),
            "heatmap": f"data:image/png;base64,{heatmap_b64}",
            "taskId": f"T-{np.random.randint(1000, 9999)}",
            # 调试信息，方便看为什么没检出
            "debug": {"area": area, "mean": mean_inside, "thr": float(thr)}
        }
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)