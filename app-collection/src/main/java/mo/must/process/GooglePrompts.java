package mo.must.process;

import lombok.Data;
import lombok.experimental.Accessors;

import java.util.Date;

@Data
@Accessors(chain = true)
public class GooglePrompts {
    private String appId;
    private String global;
    private String component;
    private String adjacent;
    private String restrictive;
    private String guiding;
    private Date updateDate;
}
