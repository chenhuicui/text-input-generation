package mo.must.process.domain;

import lombok.Data;
import lombok.experimental.Accessors;

import java.util.Date;

@Data
@Accessors(chain = true)
public class SearchApkLastestVersionRsp {
    private String url;
    private String vername;
    private String vtype;
    private String description;
    private Date updateTime;
    private String minAndroidVersion;
}
