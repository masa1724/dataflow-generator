package example.mapper;
import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface ShippingMapper {
    int createRequests();
    int createShipments();
    int markRequestsShipped();
    int updateShipmentTime();
}
