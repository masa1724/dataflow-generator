package example.mapper;
import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface RegistrationManageMapper {
    int recordJ001();
    java.util.Map<String, Object> findJ001Registration();
}
