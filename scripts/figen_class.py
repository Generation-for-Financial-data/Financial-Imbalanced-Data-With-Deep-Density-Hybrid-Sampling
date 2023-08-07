from typing import List, Tuple
from sdv.single_table import CTGANSynthesizer
from sklearn.mixture import GaussianMixture
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sdv.metadata import SingleTableMetadata
from sklearn.neighbors import KernelDensity
from sdv.lite import SingleTablePreset

# push + prd

# imbalanced에 data level로 해결하는 모델
class FiGen:
    def __init__(self, ratio: float, index: List[str]):
        """
        고정적으로 사용하는 값을 저장
        
        Args:
            ratio (float): small class+생성된 데이터와 large class의 비율 
            index (List[int]): 범주형, 연속형 구분하기 위한 연속형 변수의 컬럼명 인덱스       
        """
        self.result = 0
        self.ratio = ratio
        self.index = index

    def extract_middle_percent(self, data: pd.DataFrame, start: float, last: float):
        """
        데이터의 분포 중 중간 부분을 추출 
        
        Args:
            data : 입력 데이터
            start : 추출 시작 percentile 
            last : 추출 끝 percentile
        Returns:    
            데이터의 분포 중 중간 부분을 추출하여 리턴
        """

        scaler = StandardScaler()
        data_scaled = scaler.fit_transform(data.values)
        kde = KernelDensity(kernel="gaussian", bandwidth=0.5).fit(
            data_scaled
        )  ##TODO: 계산이 안터지도록 하기, gmm으로 변경
        log_prob = kde.score_samples(data_scaled)
        prob = np.exp(log_prob)
        threshold_low, threshold_high = np.percentile(prob, [start, last])
        mask = np.logical_and(prob >= threshold_low, prob <= threshold_high)
        data_middle = data[mask]

        if len(data_middle) > 0:
            return data_middle
        else:
            print("No middle 50% found, returning original data")
            return []

    def find_categorical(
        self,
        suitable_generated_small_X: pd.DataFrame,
        categorical_small_X: pd.DataFrame,
        small_X: pd.DataFrame,
    ):  # ****************
        """
        생성된 연속형변수와 기존 연속형 변수의 cosine simmilarity를 기준으로 가장 가까운 기존 변수를 찾은 후 해당 변수의 범주형 값을 가져옴
        
        Args:
            suitable_generated_small_X : 생성된 적합한 small class의 연속형 변수만 있는 x 
            small_X : small class의 연속형, 범주형 변수가 모두 있는 orgin x
        Returns:
            생성된 연속 변수를 범주형 변수값이 결합된 형태로 리턴 
        """

        # Min-Max 스케일링을 위한 객체 생성
        scaler = MinMaxScaler()

        # 열별 Min-Max 스케일링 수행
        suitable_generated_small_scaled_X = pd.DataFrame(
            scaler.fit_transform(suitable_generated_small_X),
            columns=suitable_generated_small_X.columns,
        )

        orgin_small_non_cat_scaled_X = pd.DataFrame(
            scaler.fit_transform(small_X[self.index]), columns=self.index
        )

        # 데이터프레임을 numpy 배열로 변환
        array_mxn = suitable_generated_small_scaled_X.values
        array_kxn = orgin_small_non_cat_scaled_X.values

        # 행렬곱 수행 (mxn과 nxk로 계산)
        result_array = np.dot(array_mxn, array_kxn)

        # 각 행에서 최대값을 가지는 열의 인덱스를 가져와서 리스트로 만들기
        max_indices = np.argmax(result_array, axis=1).tolist()

        # 가장큰 열 인덱스가 들어있는 리스트의 인덱스에 따라 범주형 값 가져오기
        synthetic_small_X = pd.concat(
            [suitable_generated_small_scaled_X, categorical_small_X.loc[max_indices]],
            axis=1,
        )

        return synthetic_small_X

    def suitable_judge(
        self, midlle_small_X: pd.DataFrame, small_X: pd.DataFrame, large_X: pd.DataFrame
    ):
        """
           generated_x : 생성된 small class x 데이터
           small_X : 원본 small class x 데이터
           large_X : 원본 large class x 데이터
        """

        center_small_X = np.mean(small_X.numpy(), axis=1, dtype=np.float64, out=None)

        radius_small_X = np.max(
            np.linalg.norm(small_X.numpy() - center_small_X, axis=1)
        )

        center_large_X = np.mean(large_X.numpy(), axis=1, dtype=np.float64, out=None)

        radius_large_X = np.max(
            np.linalg.norm(large_X.numpy() - center_large_X, axis=1)
        )

        synthetic_sample = pd.DataFrame()  # 최종 합치기

        # ctgan으로 연속형 생성 부분
        metadata = SingleTableMetadata()
        metadata.detect_from_dataframe(data=midlle_small_X)

        synthesizer = SingleTablePreset(metadata, name="FAST_ML")
        synthesizer.fit(data=midlle_small_X)

        # large class의 데이터 사이즈 만큼 데이터 생성
        synthetic_data = synthesizer.sample(
            num_rows=len(large_X)
        )  # 새로운 데이터를 생성하는 비용 vs 데이터가 적합한지 판단하는 비용

        # 합성된 개수 / 원래 클래스 개수 <= ratio 만족시 그만 생성하는 것으로
        i = 0
        while len(synthetic_sample) / len(large_X) >= self.ratio:

            z = synthetic_data.loc[i]

            # 생성된 small class 데이터가 small, large class 중 small에 가까운지, small class의 지름을 넘지는 않는지
            if (
                np.linalg.norm(z - center_small_X) < np.linalg.norm(z - center_large_X)
                and np.linalg.norm(z - center_small_X) < radius_small_X
            ):
                synthetic_sample.append(z)

            i += 1

            # 생성된 샘플을 다 검정해도 생성 비율을 만족하지 못할 경우
            if (
                i + 1 == len(large_X)
                and len(synthetic_sample) / len(large_X) < self.ratio
            ):
                i = 0
                synthetic_data = synthesizer.sample(num_rows=len(large_X))

        return synthetic_sample.reset_index(drop=True)

    def generate_synthetic(
        self,
        small_X: pd.DataFrame,
        large_X: pd.DataFrame,
        small_Y: pd.DataFrame,
        large_Y: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        생성된 데이터셋 + 기존 데이터셋을 합쳐 통합 데이터셋을 생성
        
        Args:
            small_X (pd.DataFrame): small class의 x
            large_X (pd.DataFrame): large class의 x
        Returns:
            생성된 데이터셋 + 기존 데이터셋을 합쳐 통합 데이터셋을 리턴
        """

        # Nan 값 제거 요청
        assert not large_X.isnull().values.any(), "large_X 입력 데이터에 NaN 값이 포함되어 있습니다."
        assert not small_X.isnull().values.any(), "small_X 입력 데이터에 NaN 값이 포함되어 있습니다."

        # 연속형 변수만 가져오는 부분
        continue_small_X = small_X[self.index]
        continue_large_X = large_X[self.index]

        # 범주형 변수만 가져오는 부분
        categorical_small_X = small_X[list(set(small_X.columns) - set(self.index))]
        categorical_large_X = large_X[list(set(small_X.columns) - set(self.index))]

        # 상위 n% 필터링 부분
        midlle_small_X = self.extract_middle_percent(
            continue_small_X, 25, 75
        )  ##TODO: 추후에 하이퍼 파라미터로 뺄 수 있음

        midlle_large_X = self.extract_middle_percent(
            continue_large_X, 15, 85
        )  ##TODO: 추후에 하이퍼 파라미터로 뺄 수 있음

        # 연속형 데이터 생성 및 데이터 적합 판단

        suitable_generated_small_X = self.suitable_judge(
            midlle_small_X, small_X, large_X
        )

        # 코사인 유사도 기반으로 가장 가까운 기존 변수의 범주형 변수 값 가져오기

        synthetic_small_X = self.find_categorical(
            suitable_generated_small_X, categorical_small_X, small_X, self.index
        )

        # small class와 large class 합치기

        origin_small_x = pd.concat(
            [midlle_small_X, categorical_small_X.loc[midlle_small_X.index]], axis=1
        )

        small_total_x = pd.concat([synthetic_small_X, origin_small_x], axis=0)

        small_total_x["target"] = small_Y[0]

        origin_large_x = pd.concat(
            [midlle_large_X, categorical_large_X.loc[midlle_large_X.index]], axis=1
        )

        origin_large_x["target"] = small_Y[0]
        total = pd.concat([small_total_x, origin_large_x], axis=0)
        return total.drop(columns=["target"]), total["target"]

    def fit(
        self,
        small_X: pd.DataFrame,
        small_Y: pd.DataFrame,
        large_X: pd.DataFrame,
        large_Y: pd.DataFrame,
    ):
        """
        데이터를 학습 시키는 함수
        Args:
            small_X (pd.DataFrame): small class의 x
            small_Y (pd.DataFrame): small class의 y
            large_X (pd.DataFrame): large class의 x
            large_Y (pd.DataFrame): large class의 y
        Returns:
            Tuple[pd.DataFrame, pd.DataFrame]: synthetic X, y
        
        """
        # 합성+ 기존 data set 생성
        synthetic_X, synthetic_Y = self.generate_synthetic(
            small_X, large_X, small_Y, large_Y
        )
        return synthetic_X, synthetic_Y
