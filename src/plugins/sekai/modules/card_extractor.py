import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Union
from PIL import Image, ImageDraw, ImageFont
import math

def _load_image(path: str) -> np.ndarray:
    img = Image.open(path).convert('RGB')
    img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return img

@dataclass
class _BBox:
    x: int
    y: int
    w: int
    h: int
    
    def area(self):
        return self.w * self.h
    def p1(self) -> Tuple[int, int]:
        return (self.x, self.y)
    def p2(self) -> Tuple[int, int]:
        return (self.x + self.w, self.y + self.h)
    def center(self) -> Tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)
    def __str__(self):
        return f"BBox(x={self.x}, y={self.y}, w={self.w}, h={self.h})"
    def __iter__(self):
        yield self.x
        yield self.y
        yield self.w
        yield self.h

@dataclass
class Grid:
    """
    从图片重建出的网格类型
    """
    start_x: int
    start_y: int
    size: int
    sep: int
    rows: int
    cols: int
    img: np.ndarray = None
    valid_indices: List[Tuple[int, int]] = field(default_factory=list)
    def get_all_points(self):
        """
        获取所有格子中心的坐标
        """
        for i in range(self.rows):
            for j in range(self.cols):
                cx = self.start_x + self.sep * j
                cy = self.start_y + self.sep * i
                yield (cx, cy)
    def get_all_indices(self) -> List[Tuple[int, int]]:
        """
        获取所有格子的行列索引
        """
        return [(i, j) for i in range(self.rows) for j in range(self.cols)]
    def get_valid_indices(self) -> List[Tuple[int, int]]:
        """
        获取所有有效格子（有缩略图的格子）的行列索引
        """
        return self.valid_indices
    def get_grid_bbox(self, row_idx, col_idx) -> _BBox:
        """
        获取指定行列索引的格子边界框
        """
        if row_idx < 0 or row_idx >= self.rows or col_idx < 0 or col_idx >= self.cols:
            raise IndexError(f"Row or column index out of range: ({row_idx}, {col_idx} )in ({self.rows}, {self.cols})")
        x = self.start_x + col_idx * self.sep
        y = self.start_y + row_idx * self.sep
        return _BBox(x - self.size//2, y - self.size//2, self.size, self.size)
    def get_grid_image(self, row_idx, col_idx) -> np.ndarray:
        """
        获取指定行列索引的格子图片
        """
        bbox = self.get_grid_bbox(row_idx, col_idx)
        if self.img is None:
            raise ValueError("Grid image is not set.")
        return self.img[bbox.y:bbox.y+bbox.h, bbox.x:bbox.x+bbox.w]
    def __len__(self):
        return len(self.get_valid_indices())

@dataclass
class CardThumbnail:
    """
    卡牌缩略图数据类型
    """
    id: int
    is_aftertraining: bool
    img_path: str
    rarity: str
    attr: str
    img: Optional[np.ndarray] = None
    feature: Optional[Any] = None

@dataclass
class SingleCardExtractResult:
    """
    单张卡牌识别结果
    """
    id: int
    is_aftertraining: bool
    level: Optional[int] = None
    skill_level: Optional[int] = None
    master_rank: Optional[int] = None
    row_idx: int = None
    col_idx: int = None

@dataclass
class CardExtractResult:
    """
    卡牌识别结果，包含重建出的网格以及所有卡牌的识别结果
    """
    grid: Grid
    cards: List[SingleCardExtractResult]

@dataclass
class _ImageTemplate:
    """
    模板数据类型 type可为card, lv, slv, mr
    """
    type: str
    value: int
    img: np.ndarray

@dataclass
class _LevelFont:
    path: str
    size: int
    x_offset: int
    y_offset: int


class CardExtractor:
    """
    从图片中提取卡牌信息，调用 init() 方法来初始化提取器
    """

    TARGET_RESOLUTION = (640, 480)

    CARD_FEATURE_METHOD = 'orb'  #  orb / surf / template
    CARD_ORB_MATCH_METHOD = 'bf'  # 'bf' / 'flann'

    ORB_IMG_SCALE = 1.0
    ORB_NFEATURES = 50
    ORB_SCALE_FACTOR = 1.2 
    ORB_NLEVELS = 8
    ORB_EDGE_THRESHOLD = 10

    SURF_IMG_SCALE = 1.0
    CARD_TEMPLATE_SCALE = 0.5

    LEVEL_FONTS = [
        _LevelFont("data/utils/fonts/FOT-RodinNTLG Pro EB.otf", 20, 0, 0),
        _LevelFont("data/utils/fonts/SourceHanSansCN-Bold.otf", 20, 0, -7),
    ]
    MASTER_RANK_IMAGE_PATH = "data/sekai/assets/static_images/card/train_rank_{i}.png"

    # ================== 初始化 ================== #

    def __init__(self):
        """
        调用 init() 方法来初始化提取器
        """
        self._inited = False
        self._cards: List[CardThumbnail] = []
        self._card_templates: List[_ImageTemplate] = []
        self._lv_and_slv_templates: List[_ImageTemplate] = []
        self._mr_templates: List[_ImageTemplate] = []

    def _get_card_thumb_orb_feature(self, img: np.ndarray):
        """
        获取卡牌缩略图的ORB特征描述符
        """
        s = self.ORB_IMG_SCALE
        img = cv2.resize(img, (int(128 * s), int(128 * s)))
        region = (int(0 * s), int(33 * s), int(128 * s), int(48 * s))
        img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(
            nfeatures=self.ORB_NFEATURES, 
            edgeThreshold=self.ORB_EDGE_THRESHOLD,
            scaleFactor=self.ORB_SCALE_FACTOR,
            nlevels=self.ORB_NLEVELS
        )
        keypoints, descriptors = orb.detectAndCompute(gray, None)
        return descriptors
    
    def _get_card_thumb_surf_feature(self, img: np.ndarray):
        """
        获取卡牌缩略图的SURF特征描述符
        """
        s = self.SURF_IMG_SCALE
        img = cv2.resize(img, (int(128 * s), int(128 * s)))
        region = (int(0 * s), int(33 * s), int(128 * s), int(48 * s))
        img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        surf = cv2.xfeatures2d.SURF_create(hessianThreshold=400)
        keypoints, descriptors = surf.detectAndCompute(gray, None)
        return descriptors

    def _get_card_thumb_feature(self, img: np.ndarray) -> Any:
        if self.CARD_FEATURE_METHOD == 'orb':
            return self._get_card_thumb_orb_feature(img)
        elif self.CARD_FEATURE_METHOD == 'surf':
            return self._get_card_thumb_surf_feature(img)
        else:
            raise ValueError(f"Unknown CARD_FEATURE_METHOD: {self.CARD_FEATURE_METHOD}")

    def _get_card_template_img(self, img: np.ndarray, crop) -> np.ndarray:
        """
        获取卡牌缩略图的模板图片，裁剪出缩略图区域
        """
        s = self.CARD_TEMPLATE_SCALE
        img = cv2.resize(img, (int(128 * s), int(128 * s)))
        if crop:
            region = (int(0 * s), int(33 * s), int(128 * s), int(48 * s))
            img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        return img

    def _init_cards(self, cards: List[CardThumbnail]):
        """
        初始化卡牌缩略图信息，提取特征
        """
        self._cards = cards
        self._card_templates = []
        for card in self._cards:
            if card.img is None:
                card.img = _load_image(card.img_path)
            if self.CARD_FEATURE_METHOD == 'template':
                self._card_templates.append(_ImageTemplate(
                    type='card',
                    value=card,
                    img=self._get_card_template_img(card.img, crop=True),
                ))
            else:
                card.feature = self._get_card_thumb_feature(card.img)

    def _init_level_templates(self):
        """
        初始化等级和技能等级的模板
        """
        for font in self.LEVEL_FONTS:
            x_offset, y_offset = font.x_offset, font.y_offset
            font = ImageFont.FreeTypeFont(font.path, size=font.size)
            lv_region = (10, 105, 60, 20)
            lv_bg_color = (102, 68, 68)
            sr = 0
            for lv in range(1, 61):
                lv_img = Image.new('RGB', (lv_region[2], lv_region[3]), lv_bg_color)
                draw = ImageDraw.Draw(lv_img)
                text = f"Lv.{lv}"
                draw.text((0 + x_offset, 1 + y_offset), text, fill=(255, 255, 255), font=font)
                for r in range(-sr, sr + 1):
                    h = lv_region[3] + r
                    w = int(lv_region[2] * h / lv_region[3])
                    img = lv_img.resize((w, h))
                    self._lv_and_slv_templates.append(_ImageTemplate(type='lv', value=lv, img=np.array(img)))
            for slv in range(1, 5):
                slv_img = Image.new('RGB', (lv_region[2], lv_region[3]), lv_bg_color)
                draw = ImageDraw.Draw(slv_img)
                text = f"SLv.{slv}"
                draw.text((0 + x_offset, 1 + y_offset), text, fill=(255, 255, 255), font=font)
                for r in range(-sr, sr + 1):
                    h = lv_region[3] + r
                    w = int(lv_region[2] * h / lv_region[3])
                    img = lv_img.resize((w, h))
                    self._lv_and_slv_templates.append(_ImageTemplate(type='slv', value=slv, img=np.array(slv_img)))

    def _init_master_rank_templates(self):
        """
        初始化突破等级的模板
        """
        for w in range(43, 48):
            for i in range(1, 6):
                img = _load_image(self.MASTER_RANK_IMAGE_PATH.format(i=i))
                img = cv2.resize(img, (w, w))
                img = img[10:w-10, 13:w-13]
                self._mr_templates.append(_ImageTemplate(type='mr', value=i, img=img))

    def init(self, cards: List[CardThumbnail]):
        """
        给定需要匹配的卡牌缩略图信息列表，初始化提取器
        """
        self._init_cards(cards)
        self._init_level_templates()
        self._init_master_rank_templates()
        self._inited = True

    def is_initialized(self) -> bool:
        return self._inited

    # ================== 网格重建 ================== #

    def _get_closest_resolution(self, img: np.ndarray, target_res) -> Tuple[int, int]:
        """
        获取与目标分辨率最接近的能够整除的分辨率，避免在缩放时出现非整数像素的问题
        """
        w, h = img.shape[1], img.shape[0]
        area_diff, closest_res = float('inf'), (0, 0)
        for i in range(1, 10):
            res = (w // i, h // i)
            diff = abs(res[0] * res[1] - target_res[0] * target_res[1])
            if diff < area_diff:
                area_diff = diff
                closest_res = res
        for i in range(1, 10):
            res = (w * i, h * i)
            diff = abs(res[0] * res[1] - target_res[0] * target_res[1])
            if diff < area_diff:
                area_diff = diff
                closest_res = res
        return closest_res

    def _get_mean_excluding_abnormal(
        self,
        data: List[float], 
        ref_index: int = None, 
        ref_pos: float = None, 
        lb: float = 0.75, 
        ub: float = 1.5,
        round=True,
    ) -> Union[int, float]:
        """
        计算数据的均值，排除异常值，异常值定义为与参考值的偏差超过一定范围（lb, ub）的数据点
        """
        data = sorted(data)
        if ref_index is not None:
            ref_value = data[ref_index]
        elif ref_pos is not None:
            ref_value = data[int(len(data) * ref_pos)]
        else:
            raise ValueError("Either ref_index or ref_pos must be provided.")
        filtered_data = [d for d in data if lb * ref_value <= d <= ub * ref_value]
        ret = np.mean(filtered_data)
        if round:
            ret = int(np.round(ret))
        return ret

    def _reconstruct_gird_from_selected_bboxes(self, bboxes: List[_BBox]) -> Grid:
        """
        根据选择的少数bbox，重构网格
        """
        start_x = self._get_mean_excluding_abnormal([b.center()[0] for b in bboxes], ref_index=0, ub=1.1)
        start_y = self._get_mean_excluding_abnormal([b.center()[1] for b in bboxes], ref_index=0, ub=1.1)
        end_x = self._get_mean_excluding_abnormal([b.center()[0] for b in bboxes], ref_index=-1, lb=0.9)
        end_y = self._get_mean_excluding_abnormal([b.center()[1] for b in bboxes], ref_index=-1, lb=0.9)

        size = self._get_mean_excluding_abnormal([b.w for b in bboxes] + [b.h for b in bboxes], ref_pos=0.5, lb=0.75, ub=1.5)
        
        seps = []
        for b1 in bboxes:
            for b2 in bboxes:
                sep = abs(b1.center()[0] - b2.center()[0])
                if sep > 10: seps.append(sep)
                sep = abs(b1.center()[1] - b2.center()[1])
                if sep > 10: seps.append(sep)
        sep = self._get_mean_excluding_abnormal(seps, ref_index=0, ub=1.5)
        
        rows = int(np.round((end_y - start_y) / sep)) + 1 
        cols = int(np.round((end_x - start_x) / sep)) + 1

        return Grid(
            start_x=start_x,
            start_y=start_y,
            size=size,
            sep=sep,
            cols=cols,
            rows=rows,
        )

    def _calculate_grid_err(self, grid: Grid, bboxes: List[_BBox]) -> float:
        """
        验证网格和给定bbox列表的误差
        """
        err = 0.0
        points = list(grid.get_all_points())
        for bbox in bboxes:
            cx, cy = bbox.center()
            nearest_point = min(points, key=lambda p: math.sqrt((p[0] - cx) ** 2 + (p[1] - cy) ** 2))
            err += math.sqrt((nearest_point[0] - cx) ** 2 + (nearest_point[1] - cy) ** 2)
        return err / len(bboxes) if bboxes else 0.0
        
    def _reconstruct_grid_from_bbox(self, bboxes: List[_BBox], choose_ratio: float=0.2, sample_time: int = 100) -> Optional[Grid]:
        """
        每次从给定的bbox列表中随机选择一部分，重构网格，返回误差最小的网格
        """
        best_grid = None
        best_err = float('inf')
        for _ in range(sample_time):
            selected_bboxes = np.random.choice(bboxes, size=int(len(bboxes) * choose_ratio), replace=False)
            grid = self._reconstruct_gird_from_selected_bboxes(list(selected_bboxes))
            err = self._calculate_grid_err(grid, bboxes)
            if err < best_err:
                best_err = err
                best_grid = grid
        return best_grid

    def _reconstruct_grid(self, img: np.ndarray) -> Grid:
        """
        从图片中获取网格，返回一个Grid对象
        """
        # 查找图片中的bbox
        img = cv2.resize(img, self._get_closest_resolution(img, self.TARGET_RESOLUTION))
        edges = cv2.Canny(img, 250, 500)
        edges = cv2.dilate(edges, None, iterations=2)
        contours = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        bboxes: List[_BBox] = []
        shrink = 2
        for contour in contours:
            area = cv2.contourArea(contour)
            if 100 < area < 20000:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h
                if 0.95 < aspect_ratio < 1.05:
                    bboxes.append(_BBox(x + shrink, y + shrink, w - shrink * 2, h - shrink * 2))

        # 过滤掉面积过小或过大的bbox
        bboxes.sort(key=lambda x: x.area(), reverse=True)
        ref_area = bboxes[len(bboxes) // 2].area()
        bboxes = [bbox for bbox in bboxes if 0.75 * ref_area < bbox.area() < 1.5 * ref_area]

        grid = self._reconstruct_grid_from_bbox(bboxes)
        grid.img = img

        # 从后往前寻找包含缩略图的网格（通过与bbox的距离判断）
        ok = False
        for row_idx, col_idx in reversed(grid.get_all_indices()):
            if ok:
                grid.valid_indices.append((row_idx, col_idx))
            else:
                point = grid.get_grid_bbox(row_idx, col_idx).center()
                nearest_bbox = min(bboxes, key=lambda b: math.sqrt((b.center()[0] - point[0]) ** 2 + (b.center()[1] - point[1]) ** 2))
                err = math.sqrt((nearest_bbox.center()[0] - point[0]) ** 2 + (nearest_bbox.center()[1] - point[1]) ** 2)
                if err < grid.size / 2:
                    grid.valid_indices.append((row_idx, col_idx))
                    ok = True

        return grid
    
    # ================== 缩略图匹配 ================== #

    def _decl_thumb_attr(self, img: np.ndarray) -> str:
        """
        推断卡牌缩略图的属性
        """
        ATTR_COLORS = {
            'pure': (81, 151, 36),
            'cute': (150, 106, 236),
            'cool': (213, 75, 70),
            'mysterious': (185, 78, 120),
            'happy': (44, 136, 241),
        }
        img = cv2.resize(img, (128, 128))
        region = (5, 5, 15, 4)
        img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        color = np.mean(img, axis=(0, 1))
        closest_attr = min(ATTR_COLORS.keys(), key=lambda k: np.linalg.norm(np.array(ATTR_COLORS[k]) - color))
        return closest_attr
    
    def _find_best_match_by_orb_distance_bf(self, feature, ref_features, min_matches=10) -> Tuple[Optional[int], float]:
        """
        使用ORB特征BF匹配查找最接近的参考特征，返回匹配的索引和平均距离，找不到时返回(None,inf)
        """
        query_desc = feature
        best_match_idx = None
        best_avg_distance = float('inf')
        if query_desc is None:
            return best_match_idx, best_avg_distance
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        for i, ref_feature in enumerate(ref_features):
            ref_desc = ref_feature
            if ref_desc is None:
                continue
            matches = bf.match(query_desc, ref_desc)
            if len(matches) < min_matches:
                continue
            avg_distance = sum([m.distance for m in matches]) / len(matches)
            if avg_distance < best_avg_distance:
                best_avg_distance = avg_distance
                best_match_idx = i
        return best_match_idx, best_avg_distance

    def _find_best_match_by_orb_distance_flann(self, feature, ref_features, min_matches=10) -> Tuple[Optional[int], float]:
        """
        使用ORB特征FLANN匹配查找最接近的参考特征，返回匹配的索引和平均距离，找不到时返回(None,inf)
        """
        query_desc = feature
        best_match_idx = None
        best_avg_distance = float('inf')
        
        if query_desc is None:
            return best_match_idx, best_avg_distance
        
        # FLANN参数配置 - 对于ORB等二进制描述子使用LSH
        FLANN_INDEX_LSH = 6
        index_params = dict(algorithm=FLANN_INDEX_LSH,
                        table_number=6,  # 12
                        key_size=12,     # 20
                        multi_probe_level=1) # 2
        search_params = dict(checks=50)
        
        flann = cv2.FlannBasedMatcher(index_params, search_params)
        
        for i, ref_feature in enumerate(ref_features):
            ref_desc = ref_feature
            if ref_desc is None:
                continue
                
            # FLANN需要至少2个训练描述符
            if len(ref_desc) < 2:
                continue
                
            try:
                # 使用knnMatch找到最近的2个匹配
                matches = flann.knnMatch(query_desc, ref_desc, k=2)
                
                # Lowe's ratio test过滤好的匹配
                good_matches = []
                for match_pair in matches:
                    if len(match_pair) == 2:
                        m, n = match_pair
                        if m.distance < 0.7 * n.distance:  # ratio test阈值
                            good_matches.append(m)
                    elif len(match_pair) == 1:
                        # 只有一个匹配时直接添加
                        good_matches.append(match_pair[0])
                
                if len(good_matches) < min_matches:
                    continue
                    
                avg_distance = sum([m.distance for m in good_matches]) / len(good_matches)
                if avg_distance < best_avg_distance:
                    best_avg_distance = avg_distance
                    best_match_idx = i
                    
            except cv2.error:
                # FLANN匹配失败时跳过
                continue
    
        return best_match_idx, best_avg_distance

    def _match_card_thumb(self, img: np.ndarray) -> Optional[CardThumbnail]:
        """
        匹配卡牌缩略图，返回匹配到的CardThumbnail对象，如果没有匹配到则返回None
        """
        
        feature = self._get_card_thumb_feature(img)
        attr = self._decl_thumb_attr(img)   

        # 先根据属性筛选
        ref_features = [thumb.feature for thumb in self._cards if thumb.attr == attr]
        thumbs = [thumb for thumb in self._cards if thumb.attr == attr]

        match_fn = None
        if self.CARD_ORB_MATCH_METHOD == 'bf':
            match_fn = self._find_best_match_by_orb_distance_bf
        elif self.CARD_ORB_MATCH_METHOD == 'flann':
            match_fn = self._find_best_match_by_orb_distance_flann
        else:
            raise ValueError(f"Unknown CARD_ORB_MATCH_METHOD: {self.CARD_ORB_MATCH_METHOD}")
        index, score = match_fn(feature, ref_features, min_matches=10)

        return thumbs[index] if index is not None else None

    # ================== 卡牌信息提取 ================== #
    
    def _match_by_template(self, img, templates: List[_ImageTemplate], threshold=1.0) -> Tuple[Optional[_ImageTemplate], float]:
        """
        使用模板匹配查找最接近的模板，返回模板和匹配分数，找不到时返回(None, score)
        """
        best_template = None
        best_score = float('inf')
        for template in templates:
            res = cv2.matchTemplate(template.img, img, cv2.TM_SQDIFF_NORMED)
            min_val, _, _, _ = cv2.minMaxLoc(res)
            if min_val < best_score:
                best_score = min_val
                best_template = template
        return best_template if best_score < threshold else None, best_score 
    
    def _extract_card_info(self, img: np.ndarray, level_mode: Optional[str], card: Optional[CardThumbnail] = None) -> Dict[str, Optional[int]]:
        """
        从图片中提取卡牌信息，包括等级、技能等级和突破等级，可以通过传入CardThumbnail进行提前筛选
        """
        img = cv2.resize(img, (128, 128))
        lv, slv, mr = None, None, None

        lv_and_slv_templates = self._lv_and_slv_templates
        # 通过给定的level_mode筛选
        if level_mode == 'lv':
            lv_and_slv_templates = [t for t in lv_and_slv_templates if t.type == 'lv']
        if level_mode == 'slv':
            lv_and_slv_templates = [t for t in lv_and_slv_templates if t.type == 'slv']
        # 额外通过稀度和花后筛选
        if card is not None:
            VALID_LV_RANGE = {
                "rarity_1_normal": (1, 20),
                "rarity_2_normal": (1, 30),
                "rarity_3_normal": (1, 50),
                "rarity_4_normal": (1, 60),
                "rarity_birthday_normal": (1, 60),
                "rarity_3_after": (40, 50),
                "rarity_4_after": (50, 60),
            }
            key = f"{card.rarity}_{'after' if card.is_aftertraining else 'normal'}"
            if key in VALID_LV_RANGE:
                lv_and_slv_templates = [
                    t for t in lv_and_slv_templates
                    if t.type != 'lv' or (VALID_LV_RANGE[key][0] <= t.value <= VALID_LV_RANGE[key][1])
                ]

        # 匹配等级和技能等级
        region = (0, 90, 80, 38)
        lv_img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        ret, lv_score = self._match_by_template(lv_img, self._lv_and_slv_templates)
        if ret is not None:
            if ret.type == 'lv':
                lv = ret.value
            elif ret.type == 'slv':
                slv = ret.value
        else:
            lv = 1
        
        # 匹配突破等级
        region = (80, 80, 48, 48)
        mr_img = img[region[1]:region[1]+region[3], region[0]:region[0]+region[2]]
        ret, mr_score = self._match_by_template(mr_img, self._mr_templates, threshold=0.1)
        if ret is not None:
            mr = ret.value
        else:
            mr = 0

        return { 
            'lv': lv,
            'slv': slv,
            'mr': mr,
        }

    # ================== 提取函数 ================== #

    def _extract_single_card(self, img: np.ndarray, level_mode: Optional[str]) -> Optional[SingleCardExtractResult]:
        """
        从单个卡牌缩略图中提取卡牌信息，匹配失败时返回None
        """
        try:
            if self.CARD_FEATURE_METHOD == 'template':
                temp, score = self._match_by_template(
                    self._get_card_template_img(img, crop=False), 
                    self._card_templates, threshold=0.1
                )
                card = temp.value if temp is not None else None
            else:
                card = self._match_card_thumb(img)

            if card is None:
                return None
            
            info = self._extract_card_info(img, level_mode, card)
            return SingleCardExtractResult(
                id=card.id,
                is_aftertraining=card.is_aftertraining,
                level=info['lv'],
                skill_level=info['slv'],
                master_rank=info['mr'],
            )
        except Exception as e:
            print(f"Error extracting card: {e}")
            return None

    def extract_cards(self, img: Union[Image.Image, np.ndarray], level_mode: Optional[str] = None) -> CardExtractResult:
        """
        从RBG图片中提取卡牌缩略图，返回一个包含所有卡牌缩略图的列表
        :level_mode: 指定要提取的等级类型，可选'lv' 'slv'，默认为None即自动判断
        """
        assert level_mode in (None, 'lv', 'slv'), "level_mode must be one of None, 'lv', 'slv'"
        if isinstance(img, Image.Image):
            img = np.array(img)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        grid = self._reconstruct_grid(img)
        
        results: List[SingleCardExtractResult] = []
        for row_idx, col_idx in grid.get_valid_indices():
            card_img = grid.get_grid_image(row_idx, col_idx)
            res = self._extract_single_card(card_img, level_mode)
            if res is not None:
                res.col_idx = col_idx
                res.row_idx = row_idx
                results.append(res)

        if level_mode is None:
            # 自动判断卡牌 （选取最常见的类型）
            is_level = [r.level is not None for r in results]
            level_mode = 'lv' if sum(is_level) > len(results) // 2 else 'slv'

        if level_mode == 'lv':
            for r in results:
                if r.skill_level:
                    r.level = r.skill_level
                r.skill_level = None
        elif level_mode == 'slv':
            for r in results:
                if r.level:
                    r.skill_level = r.level
                r.level = None

        results.sort(key=lambda x: (x.row_idx, x.col_idx))
        return CardExtractResult(
            grid=grid,
            cards=results
        )
    

