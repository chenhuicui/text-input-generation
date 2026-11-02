package mo.must.process;

import lombok.Data;
import lombok.experimental.Accessors;

import java.util.Date;

@Data
@Accessors(chain = true)
public class GoogleApk {
    private String appId;
    private String appName;
    private Double score;
    private String developer;
    private String category;
    private String installNum;
    private String versionCode;
    private Date updateDate;
    private Integer apkFlag;
    private String minAndroid;
    private String downloadUrl;
    private String apkName;
    private Date apkDownloadDate;
    private Date inforCrawlDate;
}
