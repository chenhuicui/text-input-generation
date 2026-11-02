package mo.must.process;

import lombok.Data;
import lombok.experimental.Accessors;
import lombok.val;

import java.util.Date;

@Data
@Accessors(chain = true)
public class GoogleResults {
    private String appId;
    private Date updateDate;
    private String modelType;
    private Integer seq;
    private Integer val;
    private Integer promptStructure;
    private String texts;
}
