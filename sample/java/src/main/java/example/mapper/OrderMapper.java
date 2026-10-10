package example.mapper;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface OrderMapper {
    List<Long> findOrders();
    List<Long> findSecret();
    int updateOrders();
    void clear();
    List<Long> findOrderDetails();
    int archiveOrders();
    int deleteArchivedOrders();
}
