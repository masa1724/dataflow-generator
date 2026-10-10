package example.mapper;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface InventoryMapper {
    List<Long> findById();
    int updateStock();
}
