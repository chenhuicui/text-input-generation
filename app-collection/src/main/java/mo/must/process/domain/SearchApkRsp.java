package mo.must.process.domain;

import lombok.Data;
import lombok.experimental.Accessors;

import java.util.List;

@Data
@Accessors(chain = true)
public class SearchApkRsp {
    private String appId;
    private String url;
    private String content;
    private String versionName;
    private String updateDate;
    private String developer;
    private String category;
    private String installCount;
    private String playId;
    private List<SearchApkLastestVersionRsp> searchApkLastestVersionRsps;
}
