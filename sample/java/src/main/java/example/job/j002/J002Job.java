package example.job.j002;
import example.mapper.OrderMapper;
import example.mapper.InventoryMapper;
import example.mapper.RegistrationManageMapper;
import example.mapper.ShippingMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
@Service
public class J002Job {
    @Autowired private OrderMapper orderMapper;
    @Autowired private InventoryMapper inventoryMapper;
    @Autowired private RegistrationManageMapper registrationManageMapper;
    @Autowired private ShippingMapper shippingMapper;
    public void mainProcess() {
        inventoryMapper.findById();
        shipOrders();
        registrationManageMapper.findJ001Registration();
    }
    private void shipOrders() {
        shippingMapper.createShipments();
        shippingMapper.markRequestsShipped();
    }
    private void archiveOrders() { orderMapper.archiveOrders(); }
    private void cleanupOrders() { orderMapper.deleteArchivedOrders(); }
    private void refreshInventory() {
        inventoryMapper.findById();
        inventoryMapper.updateStock();
    }
}

