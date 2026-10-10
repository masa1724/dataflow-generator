package example.job.j001;
import example.common.JobBase;
import example.common.JobHelper;
import example.mapper.OrderMapper;
import example.mapper.UserMapper;
import example.mapper.RegistrationManageMapper;
import example.mapper.ShippingMapper;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
@Service
public class J001Job extends JobBase {
    @Autowired private OrderMapper orderMapper;
    @Autowired private UserMapper userMapper;
    @Autowired private RegistrationManageMapper registrationManageMapper;
    @Autowired private ShippingMapper shippingMapper;
    private List<Long> items;
    public void mainProcess() {
        checkInput();
        super.validateJob();
        JobHelper.checkEnvironment();
        loadOrders();
        loadUsers();
        createShippingRequests();
        items.clear();
        registrationManageMapper.recordJ001();
    }
    private void checkInput() { }
    private void createShippingRequests() { shippingMapper.createRequests(); }
    private void loadUsers() { userMapper.findById(); }
    private void loadOrders() {
        orderMapper.findOrders();
        orderMapper.findOrderDetails();
    }
    private void unreachable() { orderMapper.findSecret(); }
}



