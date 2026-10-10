package example.common;
import example.mapper.OrderMapper;
public class JobBase {
    // 共通クラスの文字列定数も、到達したジョブのSELECT(推定)として抽出するサンプル。
    protected static final String COMMON_TABLE = "REGISTRATION_MANAGE";
    protected OrderMapper baseMapper;
    protected void validateJob() { }
    protected void beforeProcess() { baseMapper.findSecret(); }
}


