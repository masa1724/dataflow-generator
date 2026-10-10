package example.job.j003;
import example.mapper.ShippingMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
/** 出荷時刻を更新する、1テーブルだけにアクセスするサンプル。 */
@Service
public class J003Job {
    // 文字列からテーブル名を補うサンプル。用途を追跡せずSELECT(推定)として抽出する。
    private static final String TARGET_TABLE = "SHIPMENTS";
    @Autowired private ShippingMapper shippingMapper;
    public void mainProcess() {
        shippingMapper.updateShipmentTime();
    }
}

