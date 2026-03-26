from .utils import *

class MasterData:
    def __init__(self, data_dir: str, region: str, name: str) -> None:
        self.data_dir = data_dir
        self.region = region
        self.name = name
        self.data: list[dict] = None
        self.data_indexed: dict[str, dict[str, list]] = {}
        self.index_keys: list[str] = []
        self.mtime = 0

    def _build_index(self):
        region, name = self.region, self.name
        try:
            if self.data is None:
                warning(f"MasterData [{region}.{name}] 构建索引发生在数据加载前")
                return
            debug(f"MasterData [{region}.{self.name}] 开始构建索引")
            self.data_indexed = {}
            for key in self.index_keys:
                ind = {}
                for item in self.data:
                    if key not in item: 
                        continue
                    k = item[key]
                    ind.setdefault(k, []).append(item)
                if ind:
                    self.data_indexed[key] = ind
            info(f"MasterData [{region}.{self.name}] 构建索引成功")
        except:
            error(f"MasterData [{region}.{self.name}] 构建索引失败")

    def _update(self):
        path = os.path.join(self.data_dir, self.region, f"{self.name}.json")
        mtime = os.path.getmtime(path)
        if mtime != self.mtime:
            try:
                self.data = load_json(path)
                self.mtime = mtime
                info(f"从本地加载 MasterData [{self.region}.{self.name}]")
                self._build_index()
            except:
                error(f"从本地加载 MasterData [{self.region}.{self.name}] 失败")
            
    def set_index_keys(self, index_keys: list[str]):
        """
        设置需要建立索引的字段列表
        """
        self.index_keys = index_keys

    def get(self) -> list[dict]:
        """
        获取数据
        """
        self._update()
        return self.data
    
    def _get_indexed(self, key: str) -> dict[str, list]:
        """
        获取key的索引
        """
        self._update()
        return self.data_indexed.get(key)

    def find_by(self, key: str, value: Any, mode='first'):
        """
        查找item[key]=value的元素，mode=first/last/all
        """
        # 使用indices优化
        ind = self._get_indexed(key)
        if ind is not None:
            ret = ind.get(value)
            if not ret: 
                if mode == 'all': return []
                else: return None
            if mode == 'first': return ret[0]
            if mode == 'last':  return ret[-1]
            if mode == 'all': return ret
            raise ValueError(f"未知的查找模式: {mode}")
        # 没有索引的情况下遍历查找
        return find_by(self.get(), key, value, mode)

    def collect_by(self, key: str, values: Union[list[Any], set[Any]]):
        """
        收集item[key]在values中的所有元素
        """
        # 使用索引
        ind = self._get_indexed(key)
        if ind is not None:
            ret = []
            for value in values:
                if value in ind:
                    ret.extend(ind[value])
            return ret
        # 没有索引
        data = self.get()
        values_set = set(values)
        ret = []
        for item in data:
            if item[key] in values_set:
                ret.append(item)
        return ret
                    
    def find_by_id(self, id: int):
        """
        查找id对应的元素
        """
        return self.find_by('id', id)
    
    def collect_by_ids(self, ids: Union[list[int], set[int]]):
        """
        收集id在ids中的所有元素
        """
        return self.collect_by('id', ids)
    

class MasterDataManager:
    def __init__(self, masterdata_dir: str):
        self.masterdata_dir = masterdata_dir
        self.masterdatas: dict[str, MasterData] = {}
    
    def get(self, region: str, name: str) -> MasterData:
        key = f"{region}.{name}"
        if key not in self.masterdatas:
            self.masterdatas[key] = MasterData(self.masterdata_dir, region, name)
        return self.masterdatas[key]


